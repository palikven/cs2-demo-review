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
    duels.py          # movement and ground closed in the seconds before a duel
    coach.py          # analysis -> per-death verdicts and fixes            (Claude)
    narrate.py        # stats    -> plain-language team review              (Claude)
    roles.py          # role evidence + what each role is judged on
    gates.py          # "is this signal strong enough to say out loud?"
  server/
    app.py            # serves the viewer; routes /api/chat and /api/ask
    chat.py           # what the coach is told - context assembly for the chat
    providers/        # one file per LLM vendor; anthropic.py is the only one
  viewer/
    index.html        # the 2D viewer (callouts, killfeed, coach panel, chat)
    map_calibration.json
  radars/             # radar images for the Active Duty maps
  data/               # parsed match JSONs land here
  tests/              # 390 tests
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

**Teams** splits the two sides and shows each player's live state at the
playhead: HP bar (green → amber → red), current callout, and what they're
holding — primary, pistol, every grenade, the bomb, a defuse kit. Each side's
header totals alive count and remaining HP, which is the number that actually
decides a retake.

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
Want to see what the model gets without spending anything?
```bash
python parser/coach.py data/match.json --facts-only
```

## Talking to the coach

Press **K** in the viewer (or the *Coach chat* button in the header). It's a
conversation, not a one-shot box: ask *"what did I do wrong here, that my swing
and rating went down?"*, then *"and the round before?"* without restating
anything.

Each turn is stamped with the round and tick you asked it at, because the answer
is only true for that moment. Move the playhead and ask again and the coach is
looking at the new one.

**Bring your own key.** If the server has no `ANTHROPIC_API_KEY`, the panel asks
for one. It's kept in your browser's `localStorage` and sent only to your own
localhost server, which makes the provider call — provider SDKs stay server-side
and the key never goes anywhere else. If both a browser key and a server key
exist, the browser one wins, so you're billed on the key you connected rather
than silently spending whatever the machine had set.

> `localStorage` is readable by any script on the page. That's fine for a tool
> you run on your own machine. It would **not** be fine if you ever host this —
> if you do, move key handling server-side first.

Model picker is Opus 5 / Sonnet 5 / Haiku 4.5. It's your money once you've
connected a key, so the cheap one is one click away.

### What the coach is told

Two blocks, and the order is load-bearing:

| | contents | cached |
|---|---|---|
| **stable** | system prompt, your rating / KAST / ADR / swing, every death with the callout you died in, the one you were killed from and how you entered the duel, hotspots, spacing, the team scoreboard, round-by-round results | **yes** |
| **volatile** | the tick you're paused at, then the conversation, then the question |  no |

Caching is a prefix match, so stable content has to come first and be
byte-identical across turns — the brief is built once per match and reused. It's
~2,500 tokens on a real match, well over the 512 minimum, so every follow-up
reads it back at roughly a tenth of the price instead of paying for it again.

The enemy-blindness rule below applies here too, and is the reason the answers
are worth anything: the coach gets what *you* knew at that tick, plus what the
demo proves happened afterwards. Never live enemy positions.

### Roles

The panel asks what kind of match this was before it asks anything else. A
**FACEIT pug** has no roles and the picker stays hidden — offering it anyway
invites an answer that isn't true and then feeds it to the model as fact. A
**team match** unlocks IGL / AWPer / Entry / Support / Anchor / Rifler.

It matters because the same death means opposite things depending on the job.
An entry dying first at 5v5 did their job if someone traded them; an anchor
dying first at 5v5 threw the round. So every role's rules say what to weigh
*and what to stop holding against them* — an anchor is not criticised for dying
isolated, because holding alone is the assignment, and an entry is never coached
toward survival, because an entry who stops entering isn't one. A rulebook that
only adds priorities makes every role sound like a fragger with extra homework.

Rules live in `parser/roles.py` and are shared by the chat and the batch coach —
two copies of "what an anchor is judged on" would drift and you'd never know
which one an answer came from.

**The demo won't pick for you.** Under the picker it shows what this match
actually says about the player — their share of the opening duels, AWP rounds,
grenades thrown against their own team's average — and then usually tells you
it isn't enough:

```
1W/1L of this match's 22 opening duels · AWP in 1/22 rounds · 60 nades (team avg 39)
Not lopsided enough to suggest a role — this is one match.
```

That's the honest answer for a pug. 22 rounds hold 22 opening duels shared
across ten players, so the busiest player has five — a coin landing heads five
times. `roles.suggest()` only speaks when the evidence is genuinely lopsided
(half a match holding an AWP, a third of the opening duels), needs at least 12
rounds before it says anything at all, and never guesses the IGL: nothing in a
demo distinguishes the caller, and even with voice extracted, talk time is not
leadership.

```bash
python parser/roles.py data/2v5.json      # role evidence, per player
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

**How you enter duels.** For every kill and death, from the position samples in
the seconds before it: how fast you were moving, and how much ground each side
covered *toward the other* — measured against where the other player started,
which is what separates "you walked into this" from "they ran you down". A death
card reads `running into it (186 u/s) · you closed 314u`.

Two things it deliberately does not say. It is not called *peeking*: crossing
into a sightline is a visibility question and there is no occlusion data here,
so it reports ground closed, which is arithmetic. And it carries its own lag —
the sample is a ~0.5s average ending up to 0.5s before the kill, so "standing
still" means during the approach, not at the instant of death. Hover the line
and the tooltip says exactly how stale it is.

The per-duel numbers are always stated. The *pattern* is gated: on the bundled
demo the POV dies at 39 u/s against a lobby median of 75, which looks like a
finding and does not survive a permutation test (p≈0.20 on 16 deaths, and the
sign of the comparison flips with the window size). So the fact sheet prints
`[NOT A PATTERN]` and tells the model in words not to say this player "tends to"
anything. It earns the claim across a season, not one match.

```bash
python parser/duels.py data/2v5.json      # every duel you lost, and the test
```

**Rating:** HLTV Rating 1.0 and KAST per player, shown next to each name and in
the selected-player panel. Rating 1.0 is HLTV's published formula, so the number
is the real one — 1.00 is an average player. It's also fed to the AI layers,
which otherwise rank players by raw kills.

**Everything in the selected-player panel is live.** Rating, KAST, ADR, K/D,
HS% and the entry stats are recomputed from the events up to the playhead, so
they show what the scoreboard said *at that moment* rather than at the end.
Scrub a match and watch a rating build.

That duplicates the parser's maths in JS, which is a real drift risk — so
loading with `?verify` cross-checks every player and every field against what
`parse_demo.py` computed, and logs the result to the console:

```
http://localhost:8020/viewer/index.html?match=2v5&verify
```

**Round swing:** a live win-probability bar above the team panels that moves as
the round plays, and a Swing tile that accumulates for the selected player as
you watch. Every death also carries what it cost — a death at 5v5 is the round,
one at 1v3 post-plant was already lost.

The bar is hidden during freeze time on purpose: until players respawn the demo
still reports the previous round's bodies, so any probability read off it would
be nonsense.

```
python parser/swing.py data/2v5.json              # per-player swing
python parser/swing.py data/2v5.json --calibrate  # did the model predict this match?
```

### On HLTV's ratings

HLTV replaced Rating 2.0 with 2.1 and then, in August 2025, with **Rating 3.0**,
whose largest component (~33%) is **Round Swing**. They publish the concept and
the component weights but not the formula — the win-probability model behind
Round Swing is fitted on their whole match database and is proprietary.

So this project computes:

| | what it is |
|---|---|
| **Rating 1.0** | HLTV's published formula. The real number. |
| **Round swing** | *Our* win-probability model, every coefficient written down in `parser/swing.py`, with a `--calibrate` mode that reports how well it predicted your own matches. Not HLTV's figure and won't match it. |
| `rating2_legacy_est` | A community regression against the retired Rating 2.0. Kept only for comparison with archived HLTV pages. |

Rating 3.0 itself is not computed here and can't be faithfully — quote Rating
1.0 when you need a number you can defend.

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

The chat tests went the same way, and two of them passed against deliberately
broken code before they were fixed: one asserted the brief was *identical*
across turns, which a rebuilt-from-scratch brief also satisfies (the memo it was
meant to protect could be deleted with the test still green), and one built an
even-length history so the "never start on an assistant turn" slice never
actually landed on one.

The success path against a real API is the one thing not covered — there's no
key in this environment. Everything up to the SDK boundary is, and the
hand-written SSE parser is tested against events split across chunks, batched
into one, malformed, and truncated mid-stream.

## Maps included

mirage, inferno, nuke, ancient, anubis, dust2, train, overpass, vertigo.
Radar images are Valve's, pulled from the cs2-map-icons project.

Callout labels are derived from the demo itself — the median position of every
sample taken in each named area — so they appear for any map awpy can parse,
with no hand-authored polygons. The tradeoff is that a label only shows up for
an area somebody actually walked through in that match.
