# Brag Plan: Overwatch — CS2 Team Demo Review

## What is this app?

A local CS2 demo viewer and coach. It parses a FACEIT `.dem` into positional
facts — which callout you died in, who killed you from where, whether anyone was
close enough to trade you — and reviews the match from those. No account, no
upload to anyone else's server, no subscription.

## The angle

**Every stats site tells you your K/D. This one tells you what you did wrong —
and refuses to say anything it can't prove.**

That second half is the real differentiator and nobody else does it. The product
is full of deliberate refusals: it won't show the coach enemy positions, because
you didn't have them either. It won't call three deaths a habit off one match.
It won't say the enemy picked up the gun you dropped, because the demo doesn't
record that. A coaching tool whose selling point is restraint is a genuinely
unusual thing to put in a launch video, and it's true here.

So the video is built on one contrast: the generic advice everyone gets, versus
what this thing actually says.

## Hook (first 2-3 seconds)

The line every player has been told a hundred times, alone on black:

> Work on your positioning.

It sits there long enough to be recognised, then greys out and collapses. The
hook is the recognition — everyone watching has received that advice and knows
it's useless.

## Key moments (the middle)

- **The real answer, in the product's own words.** Three lines from the README,
  arriving one at a time: *You died in Main five times. / Four of them untraded.
  / From four different angles.* Specific where the hook was vague.
- **The app actually doing it.** A demo drops in, the parse stage ticks over,
  and the real broadcast lower-third slams in: **−24% ROUND SWING**, the
  callouts, the weapon, `full buy vs their eco · $2700 lost to $500`, and the
  UNTRADED / RE-PEEK tags.
- **What it refuses to say.** Three refusals held together on screen. This is
  the thesis and the strongest thing the project has.

## Outro / punchline

> **OVERWATCH**
> It only tells you what the demo can prove.

## User flow worth showing

Yes — and it's the centrepiece of Scene 2:

1. **Entry** — a `.dem` is dropped in (`2v5.dem · 354 MB`).
2. **Key action** — the parse runs, stage text reading *"reading the demo — this
   is the slow part"* with a progress bar.
3. **Result** — the review opens on a death, and the moment card lands.

That is literally the product's three-step loop, and the third beat is the most
visually distinctive thing it makes.

## Tone

- Preset: `polished`
- Creative direction: **a tool that refuses to guess**
- Interpretation: restraint is the product's actual thesis, so restraint is the
  video's style. Four scenes, long holds, soft crossfades, no zooms, no flashes.
  Motion is quick on entry and then still. The only aggressive moment in the
  whole video is the moment card landing, and it earns it because everything
  around it is quiet.

## Format: landscape — 1920x1080
## Duration: ~25s (24.9s as rendered)

## Visual identity (from the project)

Taken from `viewer/index.html` `:root` — the video uses the app's real palette.

- Background: `#07090d`
- Panel: `#0e1218` → `#141a23` (the cards use a 160° gradient between them)
- Accent: `#3ee89a` (signature green)
- Danger / loss: `#ff4d5e` (the swing number, the death card's left rule)
- T side: `#f0a83c` · CT side: `#4fa8f5`
- Text: `#eef2f8` primary, `#8fa0b8` dim, `#5d6b80` faint
- Line: `#1e2634`, hover `#2b3547`
- Display/body font: **Inter** (`--sans`)
- Mono (all numbers, labels, tags): **JetBrains Mono / SF Mono** (`--mono`)
- Glow: `0 0 0 1px rgba(62,232,154,.25), 0 0 24px -6px rgba(62,232,154,.5)`
- Strongest visual element: **the moment card** — the broadcast lower-third with
  the swing number at 38px mono, a 3px `--danger` left rule, and the tag row.

## Names on screen — fictional

The bundled demo is a real FACEIT match, so the player handles in it belong to
identifiable people. **Every name shown in the video is invented**
(`ghostline`, `vayr`, `k0rrin`). The numbers, callouts, economy figures and
tags are the real ones the tool produced; only the handles are substituted.

## Share copy (draft)

Built a CS2 demo coach that won't tell you to "work on your positioning" — it
tells you that you died in Main five times, four of them untraded, and then
refuses to guess about anything the demo can't prove.

## Audio direction

- Role: **sparse professional accents over a low bed.** The music carries the
  arc; SFX mark only real motion.
- Music: `happy-beats-business-moves-vol-12-by-ende-dot-app.mp3` — 110 bpm.
  Chosen over the other four bundled tracks because it has the calmest opening
  (no strong cues before 8.74s) and the lowest mean energy of the usable set,
  which suits `polished`. Its structure happens to match the story: quiet while
  the hook lands, cues arriving exactly as the evidence stacks up.
- Music treatment: start at 0:00 at a low bed level, lift very slightly into the
  card landing, and fade out over the last ~1.2s under the logo. No ducking
  gymnastics — there's no narration to duck under.
- Music cue guidance: preset read from
  `assets/music/cues/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json`.
  - **8.74s (intensity 0.99)** — the moment card lands. This is the one cue the
    video must hit.
  - **10.93s / 13.11s** — optional accents while the card's rows settle.
  - **Sequential window: 15.84 / 17.47 / 18.56** — the three refusal lines.
    Spacing there is ~1.1s, which is every *other* beat. Do not use the raw
    0.55s beat grid for these: they are sentences, not labels, and 0.55s apart
    is unreadable. Reveal on those three cues, then hold all three together
    until the outro.
- Audio-reactive treatment: **subtle.** The accent glow on the logo may breathe
  with music RMS. Nothing else responds. No waveform bars, no pulsing cards.
- SFX posture: **sparse.** Four cues in the whole video at most.
- Audio-coupled moments:
  - the generic advice greying out — one soft, low interface sound
  - the demo file landing + progress bar — one short UI tick, nothing arcade
  - the moment card slam at 8.74s — the single impact of the video
  - each refusal line arriving — a very quiet tick, or nothing at all if it
    starts to feel like a slideshow
- Restraint rule: **no whooshes, no risers, no stingers on text.** If a sound
  isn't attached to something physically moving on screen, it doesn't go in.
  The card slam is the only moment allowed to be loud.

## Storyboard

### Scene 1 — The advice / the answer — 7.0s

Black (`#07090d`). Centred, Inter, `#eef2f8`, generous tracking:

> Work on your positioning.

Holds ~1.9s (5 words at the sentence floor), then desaturates to `#5d6b80` and
drops away. In the space it leaves, three mono lines arrive one at a time, left
aligned, each holding once arrived:

> You died in Main five times.
> Four of them untraded.
> From four different angles.

Reveal spacing ~1.1s; all three still on screen at the cut. Line 1 gets ~3.0s,
line 3 gets ~1.4s — above the floor for their length.

Sequential/interaction: yes — the generic line dies, then three lines arrive one
by one and accumulate. Nothing leaves before the cut.
Audio intent: near-silence with a low bed. The grey-out is the only sound.
Audio-coupled idea: one soft interface sound on the grey-out. No sound on the
three lines — silence here makes the card landing hit harder.
Music: low bed, the track's quiet opening (no strong cues before 8.74s).
Transition mood: soft → Scene 2

### Scene 2 — The app doing it — 6.2s

Opens on the viewer's own chrome. Brief (~1.6s): a file lands —
`2v5.dem · 354 MB` in mono — under it the real stage string
*"reading the demo — this is the slow part"* and a `#3ee89a` progress bar
filling. Then the map fades up behind and **the moment card slams in** as a
lower-third, on the **8.74s cue**.

The card is the real one, rebuilt in the app's palette — 3px `#ff4d5e` left
rule, dark gradient panel, and:

```
−24%          BRIDGE ← BRIDGE · five-seven
ROUND SWING   4v5 · 11.0s
              full buy vs their eco · $2700 lost to $500
              [UNTRADED]  [RE-PEEK +1.1S]  [ALREADY DOWN]
```

The `−24%` is 38px mono `#ff4d5e` with its red text-shadow — the biggest thing
on screen, as it is in the product. Rows settle over the following ~1.5s; card
holds to the cut.

Sequential/interaction: yes — file drop → progress fill → card slam; then the
card's economy row and tag row settle in that order.
Audio intent: the one moment the video raises its voice.
Audio-coupled idea: a short UI tick on the file landing; **the impact on the
card slam at 8.74s.** Optional quiet accents at 10.93 / 13.11 as the rows settle.
Music: bed lifts slightly through the slam.
Transition mood: soft → Scene 3

### Scene 3 — What it refuses to say — 5.8s

Back to near-black. A dim mono label, `#5d6b80`:

> WHAT IT WON'T TELL YOU

Then three lines in `#eef2f8`, arriving on the **15.84 / 17.47 / 18.56** cues
(~1.1s apart) and **all holding together** until the cut:

> Never enemy positions — you didn't have them either.
> Never a habit from one match.
> Never a claim the demo can't prove.

Hold note: because these are sentences, they accumulate rather than replace.
Line 3 lands at 18.56 and holds ~1.5s into the outro's crossfade; lines 1 and 2
have ~4s and ~2.5s. Nothing is pulled before it can be read.

Sequential/interaction: yes — three lines accumulate on spaced cues; none exit.
Audio intent: quiet and certain. The refusals should feel like statements, not
reveals.
Audio-coupled idea: a very quiet tick per line, or nothing. If it starts to feel
like a slideshow, drop the ticks entirely — the restraint rule wins.
Music: bed steady, no lift.
Transition mood: soft → Scene 4

### Scene 4 — Logo — 2.0s

`OVER` in `#8fa0b8` + `WATCH` in `#3ee89a`, mono, the header's 0.2em tracking,
with the project's real `--glow` behind it. Under it, one line in `#8fa0b8`:

> It only tells you what the demo can prove.

Sequential/interaction: none.
Audio intent: settle and stop.
Audio-coupled idea: none. Let the music carry it out alone.
Music: fade out across the last ~1.2s.
Transition mood: — (end)

**Music mood for this video:** restrained / low-energy corporate bed, used for
structure rather than hype.
**Audio summary:** near-silence under the hook, one real impact when the moment
card lands on the 8.74s cue, quiet accents under the refusals, and a clean fade
out under the logo — four cues total, none of them decorative.

---

**Scene durations:** 7.0 + 6.2 + 5.8 + 2.0 = **21.0s** ✅ (15–25s, sweet spot)
