# Overwatch — CS2 Team Demo Review

A local 2D demo viewer + hard stats + positional coaching for your FACEIT team.
Nothing to deploy, no account. Parse a demo, open a web page.

What makes it different from Leetify and scope.gg is the **positional** layer:
it doesn't just tell you your K/D, it tells you that you died in Main five times
across rounds 2/3/6/16/18, four of them untraded, from four different angles.

## What's here

```
cs2coach/
  parser/
    parse_demo.py     # .dem -> compact JSON (positions + callouts + events + stats)
    analyze.py        # positions -> where you die, who from, how isolated  (no AI)
    coach.py          # analysis -> per-death verdicts and fixes            (Claude)
    narrate.py        # stats    -> plain-language team review              (Claude)
    gates.py          # "is this signal strong enough to say out loud?"
  server/
    app.py            # serves the viewer + the live "ask about this moment" endpoint
  viewer/
    index.html        # the 2D viewer (callouts, killfeed, coach panel)
    map_calibration.json
  radars/             # radar images for the Active Duty maps
  data/               # parsed match JSONs land here
  tests/              # 135 tests
```

## One-time setup

```bash
pip install -r requirements.txt
```
Python ≥ 3.11 required (awpy needs it).

## Use it

**1. Parse a demo.** Download a match `.dem` from FACEIT, then:
```bash
python parser/parse_demo.py path/to/match.dem --pov "YourFaceitName"
```
Writes `data/match.json`. `--pov` sets who the coaching is about.

**2. Build the death review.** No API key needed — this is all arithmetic:
```bash
python parser/analyze.py data/match.json --emit
```
Writes `data/match_analysis.json`. Run it without `--emit` to read the same
thing as a text dump in the terminal.

**3. Watch it.**
```bash
python server/app.py
# then open http://localhost:8000/viewer/index.html?match=match
```
Rounds you died in get a red dot. Scrub near a death and the coach panel shows
what happened; a round you survived says so.

| Key | |
|---|---|
| `Space` | play / pause |
| `←` `→` | step one sample (~0.5s) |
| `C` | callout labels on/off |
| `U` | utility on/off |
| `V` | voice comms on/off |

**Utility** draws smokes and molotovs as areas for exactly as long as they were
actually up, flashes and HEs as brief pops where they went off, the flight path
of every grenade thrown, and a planted bomb pulsing until it's defused or
explodes. It's drawn under the players, so smoke never hides a dot.

## Voice comms

FACEIT demos record in-game voice. One extra pass pulls it out:

```
python parser/voice.py 2v5.dem
```

Then press `V` in the viewer and the comms play in sync with the playhead —
scrub anywhere and you hear what was being said at that moment. The Voice panel
lists who talked and for how long; whoever is speaking lights up, and clicking
a name mutes them.

Only in-game voice is captured. If the team was on Discord there'll be nothing
to extract, and the script says so rather than writing empty files.

Note this produces audio of identifiable people talking. It's written next to
the demo, it's gitignored, and nothing uploads it anywhere — but it is a
recording of your teammates, so treat it accordingly.

> Serve it rather than opening the file directly — browsers block `fetch()` of
> the radar and calibration from `file://`. `run_viewer.bat` does all of this.

**4. (Optional) Add the AI layer.**
```bash
set ANTHROPIC_API_KEY=sk-ant-...

python parser/coach.py data/match.json    # per-death verdicts -> match_coach.json
python parser/narrate.py data/match.json  # team-level review  -> match_notes.md
```
With the key set, `server/app.py` also enables the **ask box**: pause anywhere
and ask "should I peek here?" — answered from the state of that exact tick.

Want to see what the model gets without spending anything?
```bash
python parser/coach.py data/match.json --facts-only
```

## The one design rule that matters

The AI **never sees raw positions**. Everything spatial is turned into named
callouts and counts by `analyze.py` first, and the model works from those.

LLMs are bad at spatial reasoning — feed them coordinates and they invent
tactical nonsense. But "Palace" is not a coordinate, it's a fact Valve put in
the demo, and a count of deaths there is arithmetic. That's the line: **compute
facts in code, let the model interpret them.** If you extend this, keep it.

The same principle runs the other way too. The fact sheet tells the model what
it may *not* claim:

- patterns below a repetition threshold are marked as single incidents
- a match with no repeated death spot says "do not claim a positional habit"
- if you're *closer* to teammates when dying than when winning duels, the sheet
  says so and forbids the isolation story outright

That last one is real: on the bundled demo the median distance to support is
439u at death and 571u at a kill, so the intuitive "you're overextending" read
is exactly backwards — and it's precisely the plausible-sounding narrative a
model reaches for unprompted.

The live endpoint applies it once more: enemies are **counted, never
described**. You didn't know where they were, so neither does the coach.

## What's computed

**Per player (deterministic):** kills / deaths / assists · K/D · ADR · HS% ·
entry duels + entry win % · opening deaths · traded vs untraded deaths ·
flash assists

**Per death (deterministic):** the callout you died in and the one you were
killed from · seconds into the round · who was alive on each side · distance to
your nearest living teammate · whether you were traded · whether you were the
opening duel · whether a teammate had just lost a duel in that same spot ·
post-plant · weapon, range, and whether it came through smoke or a wall

**Rolled up:** where you keep dying and from which angles · deaths by round
phase · how isolated you are when you die versus when you win

**Utility (rendered, not yet analysed):** every smoke and molotov with its real
lifetime · flash and HE detonations · throw arcs · bomb plants, defuses and
detonations

## If parsing errors on column names

FACEIT demos occasionally name a column slightly differently. `parse_demo.py`
prints the available columns when it can't find one it needs — that's a one-line
fix in the `first_present(...)` calls.

## Tests

```bash
python -m pytest
```

The positional tests protect claims the coaching layer states as fact, so
several were checked by deliberately breaking the code to confirm they fail.
Two bugs were found that way and not by review: a liveness test whose fixture
set `hp=0` and `a=false` together (so either check alone satisfied it), and a
snapshot guard that stripped enemy positions from records that were never
returned in the first place.

## Maps included

mirage, inferno, nuke, ancient, anubis, dust2, train, overpass, vertigo.
Radar images are Valve's, pulled from the cs2-map-icons project.

Callout labels are derived from the demo itself — the median position of every
sample taken in each named area — so they appear for any map awpy can parse,
with no hand-authored polygons. The tradeoff is that a label only shows up for
an area somebody actually walked through in that match.
