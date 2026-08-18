#!/usr/bin/env python3
"""
CS2 Coach - one command from a .dem to a reviewable match.

    python review.py path/to/match.dem --pov "YourName"

Everything the review needs, in the order it has to happen, skipping any stage
whose output is already newer than its input. Measured on a 354 MB FACEIT demo:

    parse_demo.py    24.1s     .dem -> match.json (5.1 MB)
    voice.py          1.3s     in-game comms -> opus + index
    analyze.py        1.4s     match.json -> _analysis.json (47 KB)
    career.py         1.3s     pool every parsed demo -> career.json
    ------------------------------
    total            ~28s      then ~1.4s to open the viewer

WHY THIS EXISTS
---------------
The stages were always four separate commands with an order that mattered and
was not written down anywhere except a README. Getting it wrong is quiet rather
than loud: run analyze before parse and you review the previous demo; skip
career and the coach chat answers "is this always a problem for me" from a
single match without saying so.

STALENESS IS TRACKED BY MTIME, DELIBERATELY
-------------------------------------------
Every stage compares its output against its input and skips when the output is
newer. Re-running after a crash costs seconds rather than half a minute, and
re-parsing a demo correctly invalidates everything downstream of it. `--force`
overrides.

That is also why career.py can now read `_analysis.json` instead of re-analysing
each match: at eight demos that is 10.7s -> 0.7s, and it is the difference
between adding a demo being instant and being a coffee break.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARSER = ROOT / "parser"
DATA = ROOT / "data"


def newer(out, *ins):
    """True when `out` exists and is at least as new as every input."""
    out = Path(out)
    if not out.exists():
        return False
    t = out.stat().st_mtime
    return all(Path(i).exists() and t >= Path(i).stat().st_mtime for i in ins)


def run(label, cmd, skip_if=False, optional=False):
    if skip_if:
        print(f"  {label:<34} skipped (up to date)")
        return 0.0
    t = time.time()
    r = subprocess.run([sys.executable, *cmd], capture_output=True, text=True)
    dt = time.time() - t
    if r.returncode:
        tail = (r.stderr or r.stdout).strip().splitlines()
        msg = tail[-1] if tail else f"exit {r.returncode}"
        if optional:
            print(f"  {label:<34} {dt:6.1f}s  skipped: {msg}")
            return dt
        print(f"  {label:<34} {dt:6.1f}s  FAILED: {msg}")
        raise SystemExit(r.returncode)
    print(f"  {label:<34} {dt:6.1f}s")
    return dt


def main():
    ap = argparse.ArgumentParser(
        description="Parse, analyse and pool a demo in one go.")
    ap.add_argument("demo", help="path to the .dem")
    ap.add_argument("--pov", help="who the coaching is about")
    ap.add_argument("--force", action="store_true",
                    help="redo every stage even if its output looks current")
    ap.add_argument("--no-voice", action="store_true",
                    help="skip the voice pass (it writes audio of real people)")
    args = ap.parse_args()

    demo = Path(args.demo).resolve()
    if not demo.exists():
        print(f"No such demo: {demo}")
        return 1

    name = demo.stem
    match = DATA / f"{name}.json"
    analysis = DATA / f"{name}_analysis.json"
    voice = DATA / f"{name}_voice.json"
    fresh = not args.force

    print(f"\n{demo.name}  ({demo.stat().st_size / 1048576:.0f} MB)\n")
    total = 0.0

    pov = ["--pov", args.pov] if args.pov else []
    total += run("parse_demo   .dem -> match.json",
                 [str(PARSER / "parse_demo.py"), str(demo), *pov],
                 skip_if=fresh and newer(match, demo))

    if not args.no_voice:
        total += run("voice        comms -> opus",
                     [str(PARSER / "voice.py"), str(demo)],
                     skip_if=fresh and newer(voice, demo), optional=True)

    total += run("analyze      -> death review",
                 [str(PARSER / "analyze.py"), str(match), "--emit"],
                 skip_if=fresh and newer(analysis, match))

    # Career is pooled across every parsed demo, so a NEW match invalidates it
    # regardless of which demo was passed in.
    who = args.pov or _pov_of(match)
    if who:
        total += run("career       pool every demo",
                     [str(PARSER / "career.py"), "--player", who, "--emit"],
                     skip_if=fresh and newer(DATA / "career.json", analysis))

    print(f"\n  {'total':<34} {total:6.1f}s")
    print(f"\nOpen it:\n  python server/app.py\n"
          f"  http://localhost:8000/viewer/index.html?match={name}\n")
    return 0


def _pov_of(match):
    import json
    try:
        return json.loads(Path(match).read_text(encoding="utf-8")).get("pov")
    except (ValueError, OSError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
