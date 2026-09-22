#!/usr/bin/env python3
"""
CS2 Coach - accept a .dem from the browser and run the pipeline on it.

The viewer used to take a parsed `match.json`, which meant the only way to get
one was to run the parser from a terminal first. That is fine for the person who
wrote the parser and useless for anyone else: "upload your demo" is the thing
people expect, and "run four Python commands, then upload the JSON they
produce" is not a product.

HOW IT WORKS

  POST /api/upload?name=<stem>&pov=<player>   streams the body to data/demos/
  GET  /api/job/<id>                          state, stage, and the match name

The job then shells out to review.py, which already owns the pipeline and its
ordering. The server does NOT reimplement those stages: two definitions of what
a demo needs would drift, and the one that drifted would be the one nobody runs
by hand.

THE FILENAME COMES OFF THE WIRE AND BECOMES A PATH

That is the whole security surface here, so `safe_stem()` reduces it to bare
word characters and nothing else - no separators, no dots, no drive letters, no
"..". Not sanitised in the sense of "escaped"; REBUILT, keeping only characters
that cannot mean anything to a filesystem. A name that survives is one that
could not have been a path in the first place.

ONE JOB AT A TIME

Parsing pegs a core for the better part of half a minute. Two at once on a
laptop makes both slow and neither finish sooner, so a second upload is refused
with 409 rather than queued - a queue would need cancellation, ordering and a
UI, for a machine that should be running one of these at a time anyway.

THE DEMO IS KEPT, NOT DELETED

It is ~350 MB, which adds up, and deleting it after a successful parse is
tempting. But voice.py reads the demo, not the JSON, and `review.py --force`
needs it to redo anything - so a deleted demo turns a re-run into a re-download.
Kept, and the UI says where it went so the disk use is visible rather than a
surprise.
"""

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / "data" / "demos"

# A demo is a few hundred MB; a long overtime match on a high tickrate can be
# well over a gigabyte. The cap exists so a wrong Content-Length cannot fill
# the disk, not because any real demo approaches it.
MAX_DEMO_BYTES = 3 * 1024 * 1024 * 1024

# CS2 is Source 2 and its demos start "PBDEMS2\0". Source 1 (CS:GO) used
# "HL2DEMO\0" - and that is what this constant said, written from memory, which
# silently rejected every genuine CS2 demo with "that is not a demo". Now
# checked against a real file by a test, because a magic number is exactly the
# kind of thing that looks right and is not.
#
# Worth checking at all because the alternative is spending 20s parsing a JPEG
# and surfacing a crash from deep inside awpy instead of the truth.
DEMO_MAGIC = b"PBDEMS2\x00"
SOURCE1_MAGIC = b"HL2DEMO\x00"

CHUNK = 1024 * 1024

RESERVED = ({"con", "prn", "aux", "nul"}
            | {f"com{i}" for i in range(1, 10)}
            | {f"lpt{i}" for i in range(1, 10)})

_jobs = {}
_jobs_lock = threading.Lock()
_running = threading.Lock()


def safe_stem(name, fallback="demo"):
    """
    Reduce a client-supplied filename to something that cannot be a path.

    Rebuilt rather than escaped: everything outside [A-Za-z0-9_-] is dropped,
    which removes separators, dots, colons and drive letters in one rule
    instead of a blocklist that has to anticipate them. A stem that survives
    this could not have been a path to begin with.
    """
    stem = Path(str(name or "")).name            # strip any directory part
    stem = stem[:-4] if stem.lower().endswith(".dem") else stem
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", stem).strip("_-")
    stem = re.sub(r"_{2,}", "_", stem)
    stem = (stem or fallback)[:64]
    # Windows device names. On this machine (Win 11, Python 3.13) "nul.dem"
    # writes a real file, so this was NOT observed biting - it guards builds
    # where it does, on which a 350 MB upload goes to the null device and the
    # parse then sees nothing. One rule is cheaper than finding out which
    # builds care.
    if stem.lower() in RESERVED:
        stem += "_"
    return stem


def unique_stem(stem):
    """
    A stem not already taken in data/demos.

    Uploading `match.dem` twice must not silently overwrite the first one and
    quietly change what `?match=match` resolves to.
    """
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    if not (DEMO_DIR / f"{stem}.dem").exists():
        return stem
    for i in range(2, 1000):
        if not (DEMO_DIR / f"{stem}_{i}.dem").exists():
            return f"{stem}_{i}"
    return f"{stem}_{uuid.uuid4().hex[:6]}"


def new_job(stem):
    jid = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[jid] = {"id": jid, "match": stem, "state": "uploading",
                      "stage": None, "stages": {}, "error": None,
                      "started": time.time()}
    return jid


def get_job(jid):
    with _jobs_lock:
        j = _jobs.get(jid)
        return dict(j) if j else None


def _set(jid, **kw):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(kw)


def receive(rfile, length, dest, max_bytes=MAX_DEMO_BYTES):
    """
    Stream the request body to `dest`. Returns (ok, error).

    Streamed in chunks rather than read() in one go: a 350 MB demo read whole
    is 350 MB of process memory for no reason, and two uploads at once would be
    700 MB.
    """
    if length <= 0:
        return False, "empty upload"
    if length > max_bytes:
        return False, f"demo is too large ({length / 1048576:.0f} MB)"

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    got = 0
    try:
        with open(tmp, "wb") as out:
            while got < length:
                chunk = rfile.read(min(CHUNK, length - got))
                if not chunk:
                    break
                if got == 0 and not chunk.startswith(DEMO_MAGIC):
                    # Fail on the first chunk rather than after the whole
                    # upload and a 20-second parse.
                    if chunk.startswith(SOURCE1_MAGIC):
                        return False, ("that is a CS:GO demo (Source 1). This "
                                       "parses CS2 demos.")
                    return False, "that file is not a CS2 demo"
                out.write(chunk)
                got += len(chunk)
        if got != length:
            return False, "upload ended early"
        tmp.replace(dest)
        return True, None
    except OSError as exc:
        return False, f"could not write the demo ({exc.strerror or exc})"
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def reserve():
    """
    Claim the single processing slot, or return False.

    Taken BEFORE the body is read, not when the job thread starts. Checking at
    thread start means two browsers both pass the check while they are still
    uploading - the lock still stops the second parse, but only after the
    person has spent a minute pushing 350 MB up the wire to be told no.
    """
    return _running.acquire(blocking=False)


def release():
    if _running.locked():
        _running.release()


def start(jid, demo_path, pov=None):
    """
    Run review.py on the uploaded demo, in a thread, tracking stages.

    Assumes the caller already holds the slot from reserve(); the thread
    releases it when the pipeline finishes.
    """
    t = threading.Thread(target=_run, args=(jid, demo_path, pov), daemon=True)
    t.start()
    return t


def _run(jid, demo_path, pov):
    try:
        cmd = [sys.executable, "-u", str(ROOT / "review.py"),
               str(demo_path), "--progress"]
        if pov:
            cmd += ["--pov", pov]
        _set(jid, state="running", stage="parse_demo")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1)
        tail = []
        for line in proc.stdout:
            line = line.rstrip()
            tail.append(line)
            del tail[:-30]
            if not line.startswith("@@"):
                continue
            try:
                ev = json.loads(line[2:])
            except ValueError:
                continue
            stage, state = ev.get("stage"), ev.get("state")
            with _jobs_lock:
                j = _jobs.get(jid)
                if not j:
                    continue
                j["stages"][stage] = state
                if state == "running":
                    j["stage"] = stage
                if state == "failed":
                    j["error"] = explain(ev.get("note") or f"{stage} failed")
        proc.wait()

        job = get_job(jid) or {}
        if proc.returncode:
            _set(jid, state="failed",
                 error=job.get("error") or _why(tail))
        else:
            _set(jid, state="done", stage=None)
    except Exception as exc:                                  # noqa: BLE001
        _set(jid, state="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        release()


# Parser failures people will actually hit, in words that say what to do. The
# raw text is kept on the end rather than replaced - "MalformedMessage" is
# useless to a player and essential to whoever gets the bug report.
_HINTS = (
    ("MalformedMessage", "The demo is corrupt or only partly downloaded. "
                         "FACEIT downloads fail quietly - try downloading it "
                         "again."),
    ("UnknownFile", "That does not look like a demo this parser can read."),
    ("MemoryError", "Ran out of memory parsing this demo."),
    ("No such file", "The demo went missing between upload and parse."),
)


def explain(raw):
    """A parser error in words, keeping the original for the bug report."""
    for needle, plain in _HINTS:
        if needle.lower() in (raw or "").lower():
            return f"{plain}  ({raw.strip()[:120]})"
    return (raw or "the pipeline failed").strip()[:300]


def _why(tail):
    """The most useful line from a failed run, for the browser."""
    for line in reversed(tail):
        if line and not line.startswith("@@"):
            return explain(line)
    return "the pipeline failed"


def busy():
    return _running.locked()


def disk_usage():
    """Bytes held by uploaded demos, so the UI can say what it is costing."""
    if not DEMO_DIR.exists():
        return 0
    return sum(p.stat().st_size for p in DEMO_DIR.glob("*.dem"))


def free_space():
    try:
        return shutil.disk_usage(ROOT).free
    except OSError:
        return None
