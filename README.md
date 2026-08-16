# Overwatch — CS2 Team Demo Review

A local 2D demo viewer + hard stats + AI narration for your FACEIT team.
No server, no account, nothing to deploy. Parse a demo, open a web page.

## What's here

```
cs2coach/
  parser/
    parse_demo.py     # .dem -> compact JSON (positions + events + stats)
    narrate.py        # stats -> plain-language team review (uses Claude API)
  viewer/
    index.html        # the 2D viewer (open in a browser)
    map_calibration.json
  radars/             # radar images for the Active Duty maps
  data/               # parsed match JSONs land here
```

## One-time setup

```bash
pip install awpy anthropic
```
Python ≥ 3.11 required (awpy needs it).

## Use it (3 steps)

**1. Parse a demo.** Download a match `.dem` from FACEIT, then:
```bash
python parser/parse_demo.py path/to/match.dem --pov "YourFaceitName"
```
This writes `data/match.json`. `--pov` is optional (just highlights you).

**2. Watch it.** Serve the folder and open the viewer:
```bash
python -m http.server 8000
# then open http://localhost:8000/viewer/index.html
```
Click **Load Match JSON**, pick your file from `data/`. Use the round buttons,
spacebar to play/pause, arrow keys to step, and the speed toggle.

> Serve with `http.server` rather than opening the file directly — browsers
> block `fetch()` of the radar/calibration from `file://`.

**3. Get the AI review.**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
python parser/narrate.py data/match.json
```
Prints team notes and saves `data/match_notes.md`.

## The one design rule that matters

The AI **only ever sees the computed stats**, never raw positions. That's on
purpose. LLMs are bad at spatial reasoning — feed them coordinates and they
invent tactical nonsense. All the real logic (entries, trades, opening deaths,
flash assists) is deterministic Python in `parse_demo.py`. The model just
phrases those true numbers. If you extend this, keep that line: compute facts in
code, let the LLM narrate.

## If parsing errors on column names

FACEIT demos occasionally name a column slightly differently. `parse_demo.py`
prints the available columns when it can't find one it needs — send me that list
and it's a one-line fix in the `first_present(...)` calls.

## Stats currently computed (per player)

kills / deaths / assists · K/D · HS% · entry duels + entry win % ·
opening deaths · traded vs untraded deaths · flash assists

These were chosen because they're **checkable and actionable** — the opposite of
a vague "positioning score." Next things worth adding (in rough order):
multi-kill rounds, death locations heatmap, utility thrown per round,
round-win-probability swing.

## Maps included

mirage, inferno, nuke, ancient, anubis, dust2, train, overpass, vertigo.
Radar images are Valve's, pulled from the cs2-map-icons project.
