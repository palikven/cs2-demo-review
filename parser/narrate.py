#!/usr/bin/env python3
"""
CS2 Team Coach - AI narration layer (Stage 3)

Takes the DETERMINISTIC stats produced by parse_demo.py and asks an LLM to
phrase them as short, concrete team feedback. The model NEVER sees raw
coordinates - only the numbers your code already computed and verified. This is
the whole point: the intelligence lives in the stats, the LLM is a narrator.
If you skip this rule and feed positions, you get confident nonsense.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python narrate.py data/your_match.json

Output: prints feedback to console and writes data/<match>_notes.md
"""

import json
import os
import sys
from pathlib import Path

MODEL = "claude-sonnet-4-6"   # good balance of quality/cost for this


def build_facts(match):
    """Turn the stats dict into a compact, unambiguous fact sheet (text)."""
    s = match.get("stats", {})
    per = s.get("per_player", {})
    pov = match.get("pov")
    lines = [
        f"Map: {match.get('map')}",
        f"Rounds played: {s.get('rounds_played')}",
        f"POV player (focus feedback on the team, but this is who we're helping): {pov}",
        "",
        "Per-player stats (all deterministic, parsed from the demo):",
    ]
    # Only state a stat we actually measured. A field the parser could not
    # populate must be omitted, not sent as a zero - a zero reads as a finding,
    # and the model will confidently diagnose a utility problem that is really a
    # parsing gap.
    #
    # The parser cannot distinguish "nobody flashed for a teammate" from "this
    # demo did not record assistedflash". Treat the field as measured only if the
    # signal is too strong to be noise: at least two different players, and at
    # least 3% of kills. 2v5.dem records 1 flash assist across 151 kills and
    # attackerblind on 0 of 159 kills, which is not a real team's utility usage.
    total_kills = sum(st.get("kills", 0) for st in per.values())
    flashers = sum(1 for st in per.values() if st.get("flash_assists"))
    total_flash = sum(st.get("flash_assists", 0) for st in per.values())
    flash_reliable = flashers >= 2 and total_flash >= max(3, 0.03 * total_kills)

    for name, st in per.items():
        parts = [
            f"- {name}: {st['kills']}K/{st['deaths']}D/{st['assists']}A",
            f"K/D {st['kd']}",
        ]
        if st.get("adr") is not None:
            parts.append(f"ADR {st['adr']}")
        parts += [
            f"HS% {st['hs_pct']}",
            f"entry duels {st['entry_success']}/{st['entry_attempts']} "
            f"(win {st['entry_win_pct']}%)",
            f"opening deaths {st['opening_deaths']}",
            f"deaths traded {st['traded_deaths']}/{st['deaths']}",
            f"untraded deaths {st['untraded_deaths']}",
        ]
        if flash_reliable:
            parts.append(f"flash assists {st['flash_assists']}")
        lines.append(", ".join(parts))

    if not flash_reliable:
        lines += ["", "(Flash-assist data was not recorded in this demo. Do not "
                      "comment on flash usage or utility setup.)"]
    return "\n".join(lines)


SYSTEM = """You are a blunt, experienced CS2 in-game leader reviewing a team's match.
You are given ONLY verified statistics - no positions, no video. Do not invent
tactical specifics you can't support from the numbers (don't name spots, angles,
or utility you weren't told about). Base every point on a stat you were given.

Write for a competitive FACEIT team that wants honest, actionable notes - no
fluff, no praise padding. Structure:

TEAM READ (2-3 sentences): what the numbers say about how this match went.

PER-PLAYER (one tight line each, only for players with something notable):
  name — the pattern in the stats + one concrete thing to change.

NEXT MATCH (3 bullet points max): the highest-leverage adjustments.

Rules:
- If someone has high opening deaths and low entry win %, that's dying on entry
  without trades - flag it.
- If untraded deaths are high team-wide, that's a spacing/support problem.
- ADR against a low K/D means damage that isn't converting into kills; ADR below
  ~60 means the player isn't getting into fights at all. Say which.
- Only comment on a stat that appears in the sheet below. If a stat is absent it
  was not measured - say nothing about it rather than treating it as zero.
- Keep it under 250 words total. Every claim ties to a number."""


def narrate(match):
    try:
        import anthropic
    except ImportError:
        sys.exit("[!] pip install anthropic  (and set ANTHROPIC_API_KEY)")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("[!] Set ANTHROPIC_API_KEY in your environment first.")

    facts = build_facts(match)
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=700,
        system=SYSTEM,
        messages=[{"role": "user", "content":
                   f"Here are the match stats. Give the team its review.\n\n{facts}"}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python narrate.py data/your_match.json")
    path = Path(sys.argv[1])
    match = json.loads(path.read_text())
    print("[*] Facts sent to model:\n")
    print(build_facts(match))
    print("\n" + "=" * 60 + "\n")
    notes = narrate(match)
    print(notes)
    out = path.with_name(path.stem + "_notes.md")
    out.write_text(f"# Match review — {match.get('map')}\n\n{notes}\n")
    print(f"\n[+] Saved {out}")
