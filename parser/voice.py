#!/usr/bin/env python3
"""
CS2 Coach - voice comms extractor

FACEIT demos record in-game voice, and demoparser2 hands it over as raw Opus
packets tagged with the tick they were sent on. This turns that into something
a browser can play in sync with the demo timeline.

Usage:
    python voice.py 2v5.dem
    python voice.py 2v5.dem --match data/2v5.json

Output:
    data/voice/<match>/<player>.opus   one Ogg/Opus track per player
    data/<match>_voice.json            tick -> audio-offset index

How the sync works
------------------
Voice only exists while somebody is talking, so a player's track is their
speech with all the silence removed - roughly 4 minutes of audio for a 36
minute match. Playing it against demo time therefore needs a mapping, which is
what the index is: speech is grouped into utterances (a gap of over half a
second starts a new one) and each utterance records the tick it starts at and
its offset into the audio file. The viewer looks up the utterance covering the
current tick and seeks the audio element to the matching offset.

Every packet is exactly 10ms, so within one utterance the mapping is linear
and exact - no drift, no resampling, no guessing.

A note on what this is
----------------------
These are recordings of real people talking. The demo is your own match and
FACEIT gives it to the participants, but the extracted audio is still
identifiable speech: it is written next to the demo, it is gitignored, and
nothing here uploads it anywhere.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oggopus import mux  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# A silence longer than this starts a new utterance. Half a second is short
# enough to keep separate calls separate and long enough not to split a
# sentence at every breath.
UTTERANCE_GAP_SECONDS = 0.5

# CS2 voice is 10ms Opus frames (TOC configs 14 and 30, both fullband mono).
PACKET_MS = 10
SAMPLES_PER_PACKET = OPUS_RATE_MS = 480


def safe_name(name):
    """Player names are arbitrary user input and become filenames."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", name or "unknown").strip("._")
    return cleaned or "unknown"


def unique_filename(name, steamid, taken):
    """
    safe_name() is deliberately lossy - it strips separators and trailing
    punctuation - so two different players can reduce to the same filename.
    "iTrix_" and "iTrix" both become "iTrix", and CS2 names are not unique to
    begin with. Without this the second track silently overwrites the first,
    and one player's comms just vanish.
    """
    base = safe_name(name)
    fname = f"{base}.opus"
    if fname not in taken:
        return fname
    fname = f"{base}-{steamid[-4:]}.opus"          # steamids are unique
    n = 2
    while fname in taken:
        fname = f"{base}-{steamid[-4:]}-{n}.opus"
        n += 1
    return fname


def group_utterances(packets, tickrate):
    """
    packets: [(tick, bytes)] sorted by tick, for ONE player.
    Returns (ordered_packets, utterances) where each utterance is
    {s: start tick, e: end tick, a: offset into the audio file in seconds}.
    """
    gap = max(1, int(UTTERANCE_GAP_SECONDS * tickrate))
    ordered, utterances = [], []
    start = prev = None
    count_before = 0
    run = 0

    for tick, payload in packets:
        if start is None:
            start = tick
        elif tick - prev > gap:
            utterances.append({"s": start, "e": prev,
                               "a": round(count_before * PACKET_MS / 1000, 3),
                               "n": run})
            count_before += run
            start, run = tick, 0
        ordered.append(payload)
        prev = tick
        run += 1

    if start is not None:
        utterances.append({"s": start, "e": prev,
                           "a": round(count_before * PACKET_MS / 1000, 3),
                           "n": run})
    return ordered, utterances


def extract(demo_path, match_path=None):
    from demoparser2 import DemoParser

    stem = demo_path.stem
    print(f"[*] Reading voice from {demo_path.name} ...")
    records = DemoParser(str(demo_path)).parse_voice()
    if not records:
        print("[!] No voice data in this demo. FACEIT only captures in-game "
              "voice - if the team used Discord or TeamSpeak there is nothing "
              "recorded here.")
        return None
    print(f"[*] {len(records):,} voice packets")

    # names come from the parsed match when we have one; steamids otherwise
    names, tickrate = {}, 64
    match_path = match_path or (DATA_DIR / f"{stem}.json")
    if match_path.exists():
        m = json.loads(match_path.read_text())
        names = {p["steamid"]: p["name"] for p in m.get("players", [])}
        tickrate = m.get("tickrate") or 64
    else:
        print(f"[!] {match_path.name} not found - falling back to steamids for "
              f"names and {tickrate} tick. Parse the demo first for better output.")

    by_player = defaultdict(list)
    for r in records:
        by_player[str(r["steamid"])].append((r["tick"], bytes(r["bytes"])))

    out_dir = DATA_DIR / "voice" / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    players, total_bytes, taken = [], 0, set()
    for i, (sid, packets) in enumerate(sorted(by_player.items())):
        packets.sort(key=lambda p: p[0])
        ordered, utterances = group_utterances(packets, tickrate)
        # A distinct serial per stream: they are separate files here, but a
        # shared serial makes them un-muxable if anyone ever concatenates them.
        ogg = mux(ordered, samples_per_packet=SAMPLES_PER_PACKET, serial=i + 1)

        name = names.get(sid, sid)
        fname = unique_filename(name, sid, taken)
        taken.add(fname)
        (out_dir / fname).write_bytes(ogg)
        total_bytes += len(ogg)

        seconds = len(ordered) * PACKET_MS / 1000
        players.append({
            "name": name, "steamid": sid, "file": fname,
            "seconds": round(seconds, 1), "utterances": utterances,
        })
        print(f"    {name:<14} {len(utterances):>4} utterances  "
              f"{seconds:>6.0f}s  -> {fname}")

    index = {
        "match": stem,
        "tickrate": tickrate,
        "dir": f"voice/{stem}",
        "players": sorted(players, key=lambda p: -p["seconds"]),
    }
    index_path = DATA_DIR / f"{stem}_voice.json"
    index_path.write_text(json.dumps(index, separators=(",", ":")))

    talk = sum(p["seconds"] for p in players)
    print(f"[+] Wrote {len(players)} tracks ({total_bytes/1e6:.1f} MB, "
          f"{talk/60:.0f} min of speech) and {index_path.name}")
    return index_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Extract voice comms from a CS2 demo")
    ap.add_argument("demo", help="path to the .dem file")
    ap.add_argument("--match", default=None,
                    help="parsed match JSON, for player names (default data/<demo>.json)")
    args = ap.parse_args()

    demo = Path(args.demo).expanduser().resolve()
    if not demo.exists():
        sys.exit(f"[!] File not found: {demo}")
    extract(demo, Path(args.match).expanduser().resolve() if args.match else None)
