#!/usr/bin/env python3
"""
CS2 Team Coach - local server (Stage 5)

Serves the viewer AND answers live questions about a specific moment:
pause anywhere, ask "should I peek here?", and get an answer grounded in the
actual state of that tick rather than general advice.

Why stdlib http.server and not FastAPI
--------------------------------------
This is one POST route. FastAPI + uvicorn would be two more dependencies on a
project that has four, for validation and async we don't use and an OpenAPI
schema nobody will read. run_viewer.bat already ran `python -m http.server`,
so this keeps the "no install step" property the project had before.

The API key lives here and never reaches the browser. The viewer talks to
/api/*, this process talks to Anthropic.

Usage:
    set ANTHROPIC_API_KEY=sk-ant-...
    python server/app.py            # http://localhost:8000/viewer/index.html
"""

import argparse
import json
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

from analyze import snapshot  # noqa: E402

MODEL = "claude-opus-5"
MAX_QUESTION = 500
MAX_BODY = 64 * 1024

SYSTEM = """You are a CS2 coach answering a question about one exact moment in a \
demo the player is reviewing.

You are given the state of that moment: the round, how far into it, where the \
player is standing (by callout), their health, where each living teammate is and \
how far away, how many enemies are alive, and what has already happened this round.

Two things you are deliberately NOT given, because the player didn't have them \
either: where the enemies are, and what happens next. Answer from the information \
in the snapshot only. If the honest answer depends on something you weren't told - \
utility, comms, what the enemy did last round - say which, briefly, instead of \
guessing.

Be direct and specific to the callouts involved. Two or three sentences. The player \
is mid-review and wants a read, not an essay."""


def load_match(name, _cache={}, _lock=threading.Lock()):
    """Match JSONs are megabytes; parse each one once per process."""
    with _lock:
        if name not in _cache:
            path = ROOT / "data" / f"{name}.json"
            # `name` comes off the wire - resolve it and refuse anything that
            # escapes data/, so a crafted match name can't read the filesystem.
            path = path.resolve()
            if not path.is_file() or (ROOT / "data").resolve() not in path.parents:
                return None
            _cache[name] = json.loads(path.read_text())
        return _cache[name]


def describe(snap):
    """The snapshot as text. Callouts and counts only - never coordinates."""
    lines = [
        f"Round {snap['round']}, {snap['t']}s in"
        + (" (bomb is planted)" if snap["post_plant"] else ""),
        f"You are {snap['player']}, on {snap['my_side'].upper()}, "
        f"{snap['my_hp']} HP, standing in {snap['my_place'] or 'an unnamed area'}.",
        f"Alive: you + {snap['teammates_alive']} teammates vs "
        f"{snap['enemies_alive']} enemies.",
        "",
        "Your teammates:",
    ]
    if snap["teammates"]:
        for m in snap["teammates"]:
            lines.append(f"- {m['name']}: {m['place'] or 'unknown area'}, "
                         f"{m['hp']} HP, {int(m['dist'])} units from you"
                         if m.get("dist") is not None else
                         f"- {m['name']}: {m['place'] or 'unknown area'}, {m['hp']} HP")
    else:
        lines.append("- none, you are the last one alive")

    lines += ["", "You do not know where the enemies are.", "", "This round so far:"]
    if snap["round_so_far"]:
        for k in snap["round_so_far"]:
            who = "You lost" if k["mine"] else "You got"
            lines.append(f"- {k['t']}s: {who} a player - {k['victim']} died in "
                         f"{k['victim_place'] or '?'}, killed from "
                         f"{k['attacker_place'] or '?'}")
    else:
        lines.append("- nobody has died yet")
    return "\n".join(lines)


def ask_model(snap, question):
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        thinking={"type": "adaptive"},
        # medium, not high: this is interactive and the player is waiting.
        output_config={"effort": "medium"},
        system=[{"type": "text", "text": SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user",
                   "content": f"{describe(snap)}\n\nQuestion: {question}"}],
    )
    if resp.stop_reason == "refusal":
        return "(The model declined to answer that one.)"
    return "".join(b.text for b in resp.content if b.type == "text").strip()


class Handler(SimpleHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_range(self):
        """
        Byte-range support, so the browser can seek inside a voice track.

        SimpleHTTPRequestHandler answers every request with the whole file and
        no Accept-Ranges header. An <audio> element treats that as a stream of
        unknown length: duration reads back NaN and seeking to a point that
        isn't buffered yet silently does nothing. Voice playback is entirely
        seeking - every utterance jump is a seek - so it needs real 206s.

        Returns True if it handled the request.
        """
        rng = self.headers.get("Range")
        if not rng or not rng.startswith("bytes="):
            return False
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return False
        size = os.path.getsize(path)
        spec = rng[6:].split(",")[0].strip()
        try:
            first, _, last = spec.partition("-")
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            else:
                # suffix form: "bytes=-500" means the final 500 bytes
                start, end = max(0, size - int(last)), size - 1
        except ValueError:
            return False
        if start >= size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return True
        end = min(end, size - 1)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        return True

    def end_headers(self):
        # Advertise range support on ordinary 200s too, so the browser knows it
        # may seek without having to probe first.
        if self.path.startswith("/data/voice/"):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_GET(self):
        if self.path == "/api/status":
            # Lets the viewer hide the ask box rather than offer a button that
            # can only fail.
            return self._json(200, {"llm": bool(os.environ.get("ANTHROPIC_API_KEY")),
                                    "model": MODEL})
        try:
            if self._serve_range():
                return
        except (BrokenPipeError, ConnectionResetError):
            return          # the browser closed the stream mid-seek; normal
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/ask":
            return self._json(404, {"error": "no such endpoint"})
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return self._json(503, {"error": "ANTHROPIC_API_KEY is not set on the "
                                             "server, so live questions are off."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._json(400, {"error": "bad request body"})
            req = json.loads(self.rfile.read(length))
            question = (req.get("question") or "").strip()[:MAX_QUESTION]
            if not question:
                return self._json(400, {"error": "empty question"})

            match = load_match(req.get("match") or "")
            if match is None:
                return self._json(404, {"error": f"no match named {req.get('match')!r}"})

            snap = snapshot(match, int(req.get("tick")), req.get("player"))
            if snap is None:
                return self._json(400, {"error": "no frame at that tick"})

            return self._json(200, {"answer": ask_model(snap, question),
                                    "round": snap["round"], "t": snap["t"]})
        except Exception as exc:                      # noqa: BLE001
            self.log_error("ask failed: %r", exc)
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description="Serve the viewer + the live coach.")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[!] ANTHROPIC_API_KEY not set - the viewer will work, but live "
              "questions will be disabled.")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port),
                              partial(Handler, directory=str(ROOT)))
    print(f"[+] http://localhost:{args.port}/viewer/index.html?match=2v5")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] bye")


if __name__ == "__main__":
    main()
