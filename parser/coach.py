#!/usr/bin/env python3
"""
CS2 Team Coach - positional coaching layer (Stage 4)

narrate.py reviews the scoreboard. This reviews the map: every death gets a
verdict and a fix, every round with something to say gets a note, and the
habits that repeat across the match get named.

The model is given callout NAMES and COUNTS computed by analyze.py - never
coordinates, never the raw demo. That is the project's one hard rule, and it
is what makes the output checkable: every claim traces back to a number you
can go and count yourself.

Output is structured JSON keyed by (round, tick) so the viewer can anchor each
tip to the exact moment on the timeline.

Usage:
    set ANTHROPIC_API_KEY=sk-ant-...
    python coach.py data/your_match.json [--player NAME]

Writes data/<match>_coach.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import analyze  # noqa: E402

MODEL = "claude-opus-5"


# --------------------------------------------------------------------------
# what we ask for back
# --------------------------------------------------------------------------
# Kept deliberately flat. Structured outputs don't support recursive schemas or
# numeric/length constraints, and the SDK strips anything it can't send - so a
# schema that leans on those validates locally and silently loosens on the wire.
class DeathTip(BaseModel):
    round: int = Field(description="Round number, copied exactly from the death record.")
    tick: int = Field(description="Game tick, copied exactly from the death record. "
                                  "This anchors the tip to the timeline, so it must match.")
    verdict: str = Field(description="What actually went wrong, one sentence, "
                                     "referring to the callouts by name.")
    tip: str = Field(description="What to do instead next time. Concrete and "
                                 "specific to this spot, not general advice.")


class RoundTip(BaseModel):
    round: int
    tip: str = Field(description="One thing to change about how this round was played.")


class Pattern(BaseModel):
    title: str = Field(description="Short name for the habit, e.g. 'Dying in Main untraded'.")
    detail: str = Field(description="The counts that support it. Every number here "
                                    "must come from the fact sheet.")
    tip: str = Field(description="The fix.")


class MatchCoaching(BaseModel):
    summary: str = Field(description="2-3 sentences on how this player's map "
                                     "positioning went across the match.")
    patterns: list[Pattern] = Field(description="Habits that repeat. Only ones the "
                                                "fact sheet marks as notable.")
    deaths: list[DeathTip] = Field(description="One entry per death worth commenting "
                                               "on. Skip deaths where nothing was wrong.")
    rounds: list[RoundTip] = Field(description="Round-level notes, only where there "
                                              "is something specific to say.")


# --------------------------------------------------------------------------
# fact sheet
# --------------------------------------------------------------------------
def _place(p):
    return p or "an unnamed area"


_MOTION_WORDS = {
    "static": "you were standing still",
    "walking": "you were moving",
    "running": "you were at full running speed",
}
_CLOSED_WORDS = {
    "victim": "you closed the ground into this duel",
    "attacker": "they closed the ground onto you",
    "mutual": "you both walked into it",
    "neither": "neither of you crossed ground - this was a sightline you were "
               "both already in",
}


def _approach_tags(d):
    """
    How the duel was approached, in words, with the measurement's limits in it.

    The lag matters enough to state: the sample ends up to half a second before
    the kill, so "standing still" means during the approach, not at the instant
    of death. A model told the first will happily assert the second.
    """
    out = []
    if d.get("motion") and d.get("speed") is not None:
        lag = d.get("lag_s")
        window = (f" in the half-second before it (sampled {lag:.1f}s early)"
                  if lag is not None else " in the half-second before it")
        out.append(f"{_MOTION_WORDS[d['motion']]} at {d['speed']:.0f} u/s{window}")
    if d.get("motion_other"):
        out.append(f"the attacker was {d['motion_other']}")
    if d.get("closed_by"):
        detail = ""
        if d.get("gap_start") is not None and d.get("victim_closed") is not None:
            detail = (f" (gap {d['gap_start']:.0f}u; you closed "
                      f"{d['victim_closed']:+.0f}u, they closed "
                      f"{d.get('attacker_closed', 0):+.0f}u)")
        out.append(_CLOSED_WORDS[d["closed_by"]] + detail)
    return out


def _duel_lines(prof):
    """The aggregate, which usually amounts to 'do not generalise from this'."""
    if not prof or not prof.get("deaths_measured"):
        return []
    lines = ["", "HOW THIS PLAYER ENTERS DUELS (movement in the ~0.5s before each)"]
    lines.append(
        f"- Median speed when dying: {prof['speed_at_death']:.0f} u/s. "
        f"When getting a kill: {prof['speed_at_kill']:.0f} u/s. "
        f"Everyone else in this match dies at "
        f"{prof['lobby_speed_at_death']:.0f} u/s. "
        f"(Running is ~215 u/s with a rifle; you cannot shoot accurately above "
        f"~140.)")
    if prof.get("approaches_measured"):
        lines.append(
            f"- {prof['closed_into_death']} of {prof['approaches_measured']} "
            f"deaths came after this player closed ground into the duel. The "
            f"rest happened across a sightline they were already in.")
    if prof.get("claimable"):
        lines.append(f"- [NOTABLE] {prof['note']} "
                     f"(p={prof['p_vs_lobby']}, n={prof['deaths_measured']}). "
                     f"This one is strong enough to state as a habit.")
    else:
        lines.append(
            f"- [NOT A PATTERN] {prof['note']} The difference above tests at "
            f"p={prof['p_vs_lobby']} on {prof['deaths_measured']} deaths, which "
            f"is not enough to separate it from chance. Cite individual duels by "
            f"tick; do NOT say this player 'always' or 'tends to' die moving or "
            f"standing.")
    return lines


def build_facts(a, match, role=None):
    """
    Flatten the analysis into an unambiguous text fact sheet.

    Pure function, no API call - this is the thing worth testing, because if
    the sheet is wrong the coaching is wrong no matter how good the model is.
    """
    t = a["totals"]
    lines = [
        f"Player: {a['player']}",
        f"Map: {a['map']}",
        f"Rounds played: {a['rounds_played']}",
        f"Deaths: {t['deaths']}   Kills: {t['kills']}",
    ]

    # How the match actually went for this player, and how that compares to the
    # rest of the server. Without it the model reads a list of deaths with no
    # sense of whether this was a good game or a bad one, and coaches every
    # match as though it were a disaster.
    per = ((match or {}).get("stats") or {}).get("per_player") or {}
    me = per.get(a["player"])
    if me and me.get("rating") is not None:
        lines.append(
            f"HLTV Rating 1.0: {me['rating']} (1.00 is an average player) | "
            f"KAST {me.get('kast')}% | ADR {me.get('adr')} | "
            f"K/D {me.get('kd')} | {me.get('kpr')} kills and "
            f"{me.get('dpr')} deaths per round")
        rated = sorted((s["rating"], n) for n, s in per.items()
                       if s.get("rating") is not None)
        rank = [n for _, n in rated][::-1].index(a["player"]) + 1
        lines.append(f"That is rank {rank} of {len(rated)} on the server "
                     f"(best {rated[-1][0]}, worst {rated[0][0]}).")

    sw = a.get("swing") or {}
    if sw.get("swing_per_round_pct") is not None:
        lines.append(
            f"Round swing: {sw['swing_per_round_pct']:+.2f}% per round "
            f"(gained {sw['swing_gained_pct']:.0f}%, lost "
            f"{sw['swing_lost_pct']:.0f}% across the match). This is OUR model "
            f"of round-win probability, not HLTV's Rating 3.0 figure - use it "
            f"to compare deaths against each other, not against HLTV numbers.")

    lines += [
        "",
        "DEATHS (each line is one death; `tick` is its id - copy it exactly)",
    ]
    for d in a["deaths"]:
        bits = [
            f"- round {d['round']} tick {d['tick']} at {d['t']}s:",
            f"died in {_place(d['victim_place'])}",
            f"killed from {_place(d['attacker_place'])}",
            f"by {d['weapon'] or 'unknown weapon'}",
            f"({d['alive_own']}v{d['alive_enemy']}, {d['man_state']})",
        ]
        tags = []
        if d["opening"]:
            tags.append("opening duel of the round")
        tags.append("traded" if d["traded"] else "NOT traded")
        if d["repeek"]:
            tags.append(f"a teammate died in the same spot {d.get('repeek_after')}s earlier")
        if d.get("nearest_mate") is not None:
            tags.append(f"nearest living teammate {int(d['nearest_mate'])} units away"
                        + (" (isolated)" if d.get("isolated") else ""))
        else:
            tags.append("last player alive on the team")
        if d["post_plant"]:
            tags.append("after the bomb was planted")
        if d["awp"]:
            tags.append("killed by an AWP")
        for flag, text in (("blind", "the attacker was flashed"),
                           ("smoke", "shot through smoke"),
                           ("wallbang", "shot through a wall"),
                           ("air", "the attacker was airborne")):
            if d.get(flag):
                tags.append(text)
        tags += _approach_tags(d)
        lines.append(" ".join(bits) + " | " + "; ".join(tags))

    lines += ["", "WHERE THIS PLAYER DIES (rolled up across the match)"]
    if a["death_places"]:
        for h in a["death_places"]:
            angles = ", ".join(f"{f['place']} ({f['n']}x)" for f in h["from"])
            lines.append(
                f"- {h['place']}: {h['deaths']} deaths, {h['untraded']} untraded, "
                f"{h['repeeks']} of them re-peeking a lost duel. Rounds {h['rounds']}. "
                f"Killed from: {angles or 'unknown'}."
                + ("  [NOTABLE - enough repetition to call a habit]" if h["notable"]
                   else "  [only mention if it supports a point made elsewhere]")
            )
    else:
        lines.append("- No callout accounts for two or more deaths. Do not claim a "
                     "positional habit; there isn't one in this match.")

    lines += ["", "ROUND PHASE"]
    for phase, p in sorted(a["phase_profile"].items()):
        lines.append(f"- {phase}: {p['deaths']} deaths, {p['untraded']} untraded, "
                     f"{p['man_down']} while already a man down, {p['isolated']} isolated")

    iso = a["isolation_profile"]
    lines += ["", "SPACING (distance to nearest living teammate, game units)"]
    if iso.get("median_dist_at_death") is not None:
        lines.append(f"- median when dying: {int(iso['median_dist_at_death'])}")
    if iso.get("median_dist_at_kill") is not None:
        lines.append(f"- median when getting a kill: {int(iso['median_dist_at_kill'])}")
    lines.append(f"- deaths further than {int(iso['isolated_threshold'])} units from any "
                 f"teammate: {iso['isolated_deaths']} of {iso['deaths_measured']} measured")
    if (iso.get("median_dist_at_death") is not None
            and iso.get("median_dist_at_kill") is not None
            and iso["median_dist_at_death"] <= iso["median_dist_at_kill"]):
        lines.append("- NOTE: this player is no more isolated when dying than when "
                     "winning duels. Spacing is not the problem in this match; do not "
                     "claim it is.")

    lines += _duel_lines(a.get("duel_profile"))

    lines += ["", "TOTALS",
              f"- untraded deaths: {t['untraded']} of {t['deaths']}",
              f"- opening deaths: {t['opening_deaths']}",
              f"- deaths re-peeking a duel a teammate just lost: {t['repeek_deaths']}",
              f"- deaths while already a man down: {t['deaths_while_down']}",
              f"- deaths to an AWP: {t['deaths_to_awp']}"]

    # Last, because it reframes everything above it: what this player was
    # actually trying to do decides which of these numbers is a problem.
    import roles as rolesmod
    guidance = rolesmod.guidance(role)
    if guidance:
        lines += ["", f"THIS PLAYER'S ROLE: {role.strip().upper()}", guidance]
    return "\n".join(lines)


SYSTEM = """You are an experienced CS2 coach reviewing one player's map movement \
from a single match.

You are given a fact sheet computed directly from the demo. It lists every death \
with the callout the player died in, the callout the killer shot from, who was \
alive, how far the nearest teammate was, and whether the death was traded.

Base every statement on a line in that sheet. If something isn't in it, you don't \
know it: you have no video, no utility usage, no comms, and no information about \
what the enemy intended. Don't infer a smoke, a flash, or a teammate's position \
that you weren't told about.

Where the sheet marks a pattern NOTABLE, it has passed a repetition threshold and \
is safe to build a point on. Where it doesn't, treat it as a single incident. \
Where the sheet explicitly says not to claim something, don't claim it.

Write for a competitive player who wants to know what to change. Be specific to \
the callouts involved - "stop taking the Main fight alone before your team is set" \
is useful, "improve your positioning" is not.

For each death worth commenting on, give a verdict (what went wrong) and a tip \
(what to do instead). Skip deaths where the player did nothing wrong; losing a fair \
duel is not a mistake, and saying so about every death makes the real findings \
harder to spot. Copy `round` and `tick` exactly as given - they anchor your note to \
the match timeline.

Keep each verdict and tip to one or two sentences."""


# --------------------------------------------------------------------------
def _valid_ticks(a):
    return {(d["round"], d["tick"]) for d in a["deaths"]}


def coach(a, match, client=None):
    """
    Ask the model for structured coaching, then drop anything it invented.

    The (round, tick) validation is not defensive padding: those pairs are what
    the viewer uses to place a tip on the timeline, so a hallucinated tick puts
    real-looking advice at a moment that never happened. Cheaper to discard it
    here than to have the UI point at nothing.
    """
    if client is None:
        try:
            import anthropic
        except ImportError:
            sys.exit("[!] pip install anthropic  (and set ANTHROPIC_API_KEY)")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("[!] Set ANTHROPIC_API_KEY in your environment first.")
        client = anthropic.Anthropic()

    facts = build_facts(a, match)
    resp = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        # The system prompt is identical for every match, so cache it. The
        # fact sheet goes in the user turn, after the breakpoint, because it
        # changes every time and would invalidate the prefix.
        system=[{"type": "text", "text": SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user",
                   "content": f"Here is the fact sheet.\n\n{facts}"}],
        output_format=MatchCoaching,
    )
    out = resp.parsed_output

    known = _valid_ticks(a)
    kept, dropped = [], []
    for d in out.deaths:
        (kept if (d.round, d.tick) in known else dropped).append(d)
    if dropped:
        print(f"[!] Dropped {len(dropped)} death note(s) pointing at ticks that "
              f"aren't in the match: {[(d.round, d.tick) for d in dropped]}")
    out.deaths = kept

    usage = getattr(resp, "usage", None)
    if usage is not None:
        print(f"[*] tokens in/out {usage.input_tokens}/{usage.output_tokens}, "
              f"cache write {getattr(usage, 'cache_creation_input_tokens', 0)}, "
              f"cache read {getattr(usage, 'cache_read_input_tokens', 0)}")
    return out


def to_payload(out, a):
    """The shape the viewer fetches. Flat and small - it's loaded per match."""
    return {
        "player": a["player"],
        "map": a["map"],
        "model": MODEL,
        "summary": out.summary,
        "patterns": [p.model_dump() for p in out.patterns],
        "deaths": [d.model_dump() for d in out.deaths],
        "rounds": [r.model_dump() for r in out.rounds],
    }


def main():
    ap = argparse.ArgumentParser(description="Positional coaching for one player.")
    ap.add_argument("match", help="data/<match>.json from parse_demo.py")
    ap.add_argument("--player", default=None, help="defaults to the match POV")
    ap.add_argument("--facts-only", action="store_true",
                    help="print the fact sheet and exit, without calling the API")
    args = ap.parse_args()

    path = Path(args.match)
    match = json.loads(path.read_text())
    a = analyze(match, args.player)

    if not a["player"]:
        sys.exit("[!] No player to analyse - pass --player or re-parse with --pov")
    if not a["deaths"]:
        sys.exit(f"[!] {a['player']} has no classifiable deaths in this match.")

    if args.facts_only:
        print(build_facts(a, match))
        return

    print(f"[*] Coaching {a['player']} - {a['totals']['deaths']} deaths on {a['map']}")
    out = coach(a, match)

    out_path = path.with_name(path.stem + "_coach.json")
    out_path.write_text(json.dumps(to_payload(out, a), indent=1))
    print(f"\n{out.summary}\n")
    for p in out.patterns:
        print(f"  * {p.title}: {p.detail}\n    -> {p.tip}")
    print(f"\n[+] Wrote {out_path.name} "
          f"({len(out.deaths)} death notes, {len(out.rounds)} round notes)")


if __name__ == "__main__":
    main()
