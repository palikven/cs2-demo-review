# Hyperframes Composition Brief: Overwatch — CS2 Team Demo Review

## Objective

Create a short launch-style brag video for **Overwatch**, a local CS2 demo
viewer and positional coach. The video's whole argument is one contrast: the
generic advice every player gets, versus what this tool actually says — and
what it deliberately refuses to say.

## Output

- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 21s (4 scenes: 7.0 / 6.2 / 5.8 / 2.0)

## Source Material

- Project root: `C:\Users\KunnyS\Downloads\cs2coach_1\cs2coach`
- Primary files read:
  - `viewer/index.html` — the app itself; `:root` palette, the `.brand` mark,
    the `#moment` lower-third, `.tag` chips, `#uploader` progress overlay
  - `README.md` — the positional claim and the product's own framing
  - `parser/analyze.py`, `parser/economy.py` — where the death-card numbers
    come from
- Product name: **Overwatch** (rendered `OVER` + `WATCH`, the second word in
  the accent green)
- Tagline / strongest claim: *"It doesn't just tell you your K/D, it tells you
  that you died in Main five times across rounds 2/3/6/16/18, four of them
  untraded, from four different angles."*
- Key UI moment to recreate: **the moment card** — the broadcast lower-third
  from `#moment` in `viewer/index.html`. Dark gradient panel, 3px `#ff4d5e`
  left rule, the swing percentage at 38px mono with a red text-shadow, then
  callouts / weapon / man-count, an economy line, and a row of tag chips.

### Copy that must appear verbatim

- `Work on your positioning.`
- `You died in Main five times.`
- `Four of them untraded.`
- `From four different angles.`
- `reading the demo — this is the slow part`  *(the real stage string)*
- `−24%` / `ROUND SWING`
- `BRIDGE ← BRIDGE`
- `full buy vs their eco · $2700 lost to $500`
- `UNTRADED` / `RE-PEEK +1.1S` / `ALREADY DOWN`
- `Never enemy positions — you didn't have them either.`
- `Never a habit from one match.`
- `Never a claim the demo can't prove.`
- `It only tells you what the demo can prove.`

### Names on screen must be fictional

The bundled demo is a real FACEIT match, so its player handles belong to
identifiable people. **Do not use them.** If a scene needs a handle, use
`ghostline`, `vayr`, or `k0rrin`. Every number, callout, economy figure and tag
above is real output from the tool; only names are substituted.

## Creative Direction

- Tone preset: `polished`
- Creative direction: **a tool that refuses to guess**
- Interpretation: restraint is the product's actual thesis, so restraint is the
  video's style. Four scenes, long holds, soft crossfades. No zooms, no flashes,
  no parallax. Motion is quick on entry then completely still. Exactly one
  aggressive moment — the moment card landing — and it works because everything
  around it is quiet.
- Angle: every stats site reports K/D; this one names the callout you died in,
  whether anyone was close enough to trade you, and what the death cost — then
  refuses to claim anything the demo can't prove. A coaching tool whose selling
  point is restraint is unusual, and it's true here.
- Hook: `Work on your positioning.` alone on black, held long enough to be
  recognised, then greyed out and dropped.
- Outro / punchline: the wordmark, then `It only tells you what the demo can prove.`
- Avoid:
  - Generic SaaS language ("streamline", "supercharge", "unlock")
  - Abstract filler visuals — no particles, no grids, no gradient washes
  - Unrelated visual redesign; the app already has a strong identity, use it
  - Any esports-broadcast cliché the product itself doesn't use

## Visual Identity

Straight from `viewer/index.html` `:root`.

- Background: `#07090d`
- Panel gradient: `#141a23` → `#0e1218` (160°, as `.card` uses)
- Text: `#eef2f8` primary · `#8fa0b8` dim · `#5d6b80` faint
- Accent: `#3ee89a`
- Danger / loss: `#ff4d5e`
- T side `#f0a83c` · CT side `#4fa8f5` (only if a scene needs side colour)
- Line: `#1e2634`, hover `#2b3547`
- Display + body font: **Inter**
- Mono (every number, label, tag, and the wordmark): **JetBrains Mono**,
  falling back to any ui-monospace. All mono text uses wide tracking
  (0.1–0.2em) and `font-variant-numeric: tabular-nums`.
- Glow (logo only): `0 0 0 1px rgba(62,232,154,.25), 0 0 24px -6px rgba(62,232,154,.5)`
- Visual references from the project:
  - `#moment` + `.mo-swing` + `.mo-tags` — the lower-third and its tag chips
  - `.tag.bad` — red-bordered chip on a 10% red wash
  - `#uploader` / `.up-bar` — the parse progress overlay
  - `.brand` — `OVER` dim + `WATCH` accent, 0.2em tracking, uppercase

## Storyboard

Use `brag-output/brag-plan.md` as the creative contract — it carries the full
per-scene detail, reading-time floors, and audio intent.

Scene summary:

1. **The advice / the answer** — 7.0s — `Work on your positioning.` holds ~1.9s,
   greys out, and is replaced by three mono lines that arrive ~1.1s apart and
   **all stay on screen** to the cut.
2. **The app doing it** — 6.2s — a `.dem` lands, the parse stage string and
   progress bar run ~1.6s, then the moment card **slams in on the 8.74s cue**
   and its rows settle. The `−24%` is the biggest element on screen.
3. **What it refuses to say** — 5.8s — a dim `WHAT IT WON'T TELL YOU` label,
   then three refusal lines arriving on spaced cues and **accumulating**, never
   replacing.
4. **Logo** — 2.0s — wordmark with the real glow, one line under it.

## Audio

- Audio role: **sparse professional accents over a low bed.**
- Audio arc: near-silence under the hook → one real impact when the card lands →
  quiet accents under the refusals → clean fade out under the logo.
- Music: `assets/music/happy-beats-business-moves-vol-12-by-ende-dot-app.mp3`
  (110 bpm). Chosen for the calmest opening of the five bundled tracks — no
  strong cues before 8.74s — which suits `polished` and happens to match the
  story beat for beat.
- Music treatment: start at 0:00, low bed level throughout, a slight lift
  through the card landing, fade out over the last ~1.2s. No ducking (no
  narration).
- Music cue guidance: preset shipped alongside the track at
  `assets/music/happy-beats-business-moves-vol-12-by-ende-dot-app.music-cues.json`
  (`strongCues` + full `beats`; tempo 109.96, beat spacing 0.55s).
  - **Lock: 8.74s** (intensity 0.99) — the moment card lands. This is the one
    cue the video must hit. Mark it `// beat-locked: 8.74s`.
  - Optional accents: **10.93s / 13.11s** as the card's economy and tag rows
    settle.
  - **Sequential window: 15.84 / 17.47 / 18.56** for the three refusal lines —
    that is every *other* beat (~1.1s). **Do not use the raw 0.55s grid for
    these**; they are sentences and 0.55s apart is unreadable. Reveal on those
    three, then hold all three together into the outro.
  - Scene 1's three lines may use natural timing — the track has no cues before
    8.74s and readability wins there.
- Audio-reactive treatment: **subtle.** Wire music RMS to the logo's accent glow
  intensity in Scene 4 only. Nothing else responds. No waveform bars, no
  equalizers, no pulsing cards, no particles.
- Audio-coupled moments:
  - Scene 1, the advice greying out — one soft low interface sound
  - Scene 2, the `.dem` landing / progress bar — one short UI tick
  - Scene 2, **the card slam at 8.74s** — the single impact of the video
  - Scene 3, each refusal line — a very quiet tick, **or nothing**
- SFX selection guidance: four cues maximum in the whole video. Sound only
  attaches to something physically moving. **No whooshes, no risers, no
  stingers on text.** The card slam is the only moment allowed to be loud; if
  the refusal ticks start to feel like a slideshow, drop them entirely.
- SFX analysis guidance: `assets/sfx-analysis.md` (copied into the composition
  assets). Prefer low high-frequency-risk files — this is a `polished` edit and
  anything bright will read as cheap against the restraint.
- Exact SFX choice: Hyperframes picks filenames, timestamps, density and volume
  after the visual animation exists.
- Audio files: music and its cue preset are already staged in
  `brag-output/composition/assets/music/`. Copy any selected SFX into
  `brag-output/composition/assets/`.

## Hyperframes Instructions

Load `hyperframes-core`, `hyperframes-animation`, `hyperframes-creative`,
`hyperframes-keyframes` and `hyperframes-cli`, and build the composition with
the current Hyperframes conventions. `/brag` is its own workflow — do not enter
the `hyperframes` entry-point intent interview or route into its generic promo /
launch-video workflow. Prefer native Hyperframes conventions over anything in
this brief where they conflict on *implementation*; this brief is authoritative
only on product positioning, copy, tone, source material and moment selection.

Requirements:

- Show real UI from the project — the moment card is non-negotiable and is the
  centrepiece of Scene 2.
- Keep every line readable: short labels ~0.8s settled, sentences ~0.3s/word
  (min ~1.2s). Scene 1's and Scene 3's sequential lines **accumulate and hold**;
  nothing is pulled before it can be read.
- Keep total duration 15–25s (target 21s).
- Include the music and the sparse SFX layer.
- Treat the audio notes as guidance, not a fixed cue sheet — choose SFX after
  the animation exists.
- Treat cue metadata as optional timing hints; ignore any cue that hurts
  readability or pacing. Use **one** strong-cue lock (8.74s) plus at most two
  optional accents.
- Wire the logo glow to music RMS for the subtle audio-reactive requirement. If
  extraction is unavailable (no helper, or ffmpeg missing), skip it and note it
  — do not block the render.
- Use local assets only.
- Run `hyperframes check` before render — it is brag's single gate.
