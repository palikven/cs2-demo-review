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
from analyze import analyze, load_match  # noqa: E402

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


def _economy_tags(d):
    """
    The buy context and what the gun was worth.

    Both sides' buy state, always together: "you were on a full buy" alone
    invites the wrong conclusion half the time, because the mistake is the gap,
    not the number.
    """
    out = []
    if d.get("buy") and d.get("enemy_buy"):
        out.append(f"your side was on a {d['buy']} buy (${d.get('buy_value', 0)}), "
                   f"theirs on a {d['enemy_buy']} (${d.get('enemy_buy_value', 0)})")
    if d.get("gun_lost") is not None:
        line = (f"you lost a ${d['gun_lost']} weapon to someone risking "
                f"${d.get('gun_risked', 0)}")
        if d.get("armed_them"):
            # Named carefully: the demo does not record a pickup.
            line += (" - a big one-way loss. Do NOT say they picked it up; "
                     "the demo does not record that")
        out.append(line)
    return out


def _flash_lines(prof, table):
    """
    Flashes, always with the other nine players beside them.

    "You team-flash" is nagging. "You throw the second-most flashes in the match
    and 46% of the meaningful ones blind your own side, against RENAT4IK's 13%"
    is a habit with a benchmark, and the model can only say the second if it is
    handed the table.
    """
    if not prof or not prof.get("blinds"):
        return []
    lines = ["", "FLASHES (blinds of 0.7s+; one flash can catch an enemy and a "
                 "teammate at once, so these are counts and NOT a score)"]
    lines.append(
        f"- {prof['player']}: {prof['enemy_meaningful']} enemies blinded "
        f"({prof['enemy_seconds']}s), {prof['team_meaningful']} teammates "
        f"({prof['team_seconds']}s).")
    if prof.get("team_share") is not None:
        lines.append(f"- That is {prof['team_share'] * 100:.0f}% of their "
                     f"meaningful flashes landing on their own team.")
    if prof.get("worst_victim"):
        who, n = prof["worst_victim"]
        lines.append(f"- Most often blinded: {who}, {n} times.")
    for h in (prof.get("full_team_blinds") or [])[:4]:
        lines.append(f"- round {h['round']} tick {h['tick']}: {h['who']} "
                     f"blinded {h['d']}s - long enough to be out of the round.")
    if prof.get("follow_ups"):
        lines.append(f"- {len(prof['follow_ups'])} enemies died while blinded "
                     f"by them.")
    if table:
        ranked = sorted(((n, t) for n, t in table.items()
                         if t.get("team_share") is not None),
                        key=lambda kv: kv[1]["team_share"])
        if len(ranked) > 1:
            lines.append("- Everyone's team-flash share, for scale: " + ", ".join(
                f"{n} {t['team_share'] * 100:.0f}%" for n, t in ranked))
    lines.append(f"- {prof['note']}")
    return lines


def _kit_lines(prof):
    """
    Utility still in hand, always with the table.

    On the bundled match the POV is second best of ten. Handed the bare share
    the model writes that up as a weakness, because 38% sounds high in
    isolation - so the rank goes in the same sentence as the number.
    """
    if not prof or prof.get("share") is None:
        return []
    lines = ["", "UTILITY STILL IN HAND AT DEATH"]
    lines.append(
        f"- {prof['player']} died holding utility in "
        f"{prof['deaths_holding_utility']} of {prof['deaths']} deaths "
        f"({prof['share'] * 100:.0f}%) - rank {prof['rank']} of {prof['of']} "
        f"in this match, where LOWER is better.")
    if prof.get("table"):
        lines.append("- Everyone, for scale: " + ", ".join(
            f"{n} {v * 100:.0f}%" for n, v in prof["table"].items()))
    turn = prof.get("with_a_turn") or []
    if turn:
        lines.append(f"- {len(turn)} of those came in duels that gave them a "
                     f"turn to throw something:")
        for h in turn:
            lines.append(f"  - round {h['round']} tick {h['tick']} in "
                         f"{h['place']} ({h['why']}): held "
                         f"{', '.join(h['held'])}.")
    else:
        lines.append("- None of them came in a duel that gave them a turn, so "
                     "there is nothing to coach here.")
    lines.append(f"- {prof['note']}")
    return lines


def _aim_lines(prof):
    """
    Crosshair height, with the gate's verdict stated in the sheet.

    The number goes in either way, because "you are 8th of 10" is worth knowing.
    What changes is whether the model is allowed to call it a habit - handed the
    number alone it will, and on this match that would be wrong.
    """
    if not prof or prof.get("median") is None:
        return []
    lines = ["", "CROSSHAIR HEIGHT WHILE MOVING (degrees, POSITIVE is looking "
                 "DOWN, 0 is level)"]
    lines.append(
        f"- {prof['player']}: median {prof['median']}deg "
        f"(T {prof['t']}, CT {prof['ct']}), rank {prof['rank']} of "
        f"{prof['of']} where LOWER is better. Match median "
        f"{prof['match_median']}deg.")
    if prof.get("table"):
        lines.append("- Everyone, for scale: " + ", ".join(
            f"{n} {v}" for n, v in prof["table"].items()))
    if prof.get("claimable"):
        lines.append("- This holds clearly on BOTH sides, so it is about the "
                     "player rather than the positions they played. It is fair "
                     "to coach.")
    else:
        lines.append("- This does NOT hold clearly on both sides "
                     f"(above on T: {prof.get('above_on_t')}, above on CT: "
                     f"{prof.get('above_on_ct')}), so it may simply describe "
                     "where they played. State the number if asked, but do NOT "
                     "coach it as a habit.")
    lines.append(f"- {prof['note']}")
    return lines


def _comms_lines(prof):
    """
    Comms around deaths.

    The ceiling wording is repeated twice on purpose. Handed "50%" the model
    writes "you communicate half the time", and the demo cannot support that -
    it knows the mic was open, not what came out of it.
    """
    if not prof or prof.get("share") is None:
        return []
    lines = ["", "COMMS AROUND DEATHS (whether the MIC WAS OPEN - NOT what was "
                 "said, and NOT proof anything useful was called)"]
    lines.append(
        f"- {prof['player']} had the mic open within 6s of "
        f"{prof['called_after']} of {prof['deaths_with_time_to_call']} deaths "
        f"that had time left in the round ({prof['share'] * 100:.0f}%), rank "
        f"{prof['rank']} of {prof['of']}.")
    if prof.get("table"):
        lines.append("- Everyone, for scale: " + ", ".join(
            f"{n} {v * 100:.0f}%" for n, v in prof["table"].items()))
    silent = prof.get("silent_before") or []
    if silent:
        lines.append(
            f"- Nobody on their side had the mic open in the 5s before "
            f"{len(silent)} of their deaths (rounds "
            f"{', '.join(str(b['round']) for b in silent)}).")
    lines.append(f"- {prof['note']}")
    return lines


def career_lines(c):
    """
    The pooled career, when there is one.

    Kept separate from build_facts() because it is not about this match - the
    chat appends it so a question like "is Main always a problem for me" has
    something behind it other than tonight.
    """
    if not c or c.get("matches", 0) < 2:
        return []
    lines = ["", f"ACROSS {c['matches']} PARSED MATCHES ({c['deaths']} deaths "
                 f"total). This is the pooled record, NOT this match."]
    notable = [h for h in c.get("hotspots") or [] if h.get("notable")]
    if notable:
        for h in notable[:5]:
            lines.append(f"- {h['place']}: {h['deaths']} deaths across "
                         f"{h['matches']} of {c['matches']} matches "
                         f"({h['share_of_deaths'] * 100:.0f}% of all their "
                         f"deaths).")
    else:
        lines.append("- No death spot clears the bar across matches yet. Do "
                     "NOT name one anyway.")
    for key, r in (c.get("rates") or {}).items():
        line = (f"- {key.replace('_', ' ')}: {r['n']} of {r['of']} "
                f"({r['rate'] * 100:.0f}%) over {r['matches']} matches")
        if r.get("trend"):
            d = r["trend"]["delta"] * 100
            line += (f"; first half of that span {r['trend']['early'] * 100:.0f}%"
                     f" vs second half {r['trend']['late'] * 100:.0f}% "
                     f"({d:+.0f}pp)")
        lines.append(line + ".")
    lines.append(f"- {c['note']}")
    return lines


def _economy_lines(prof):
    """Match totals. Sums of prices, so no significance test applies."""
    if not prof or not prof.get("deaths_priced"):
        return []
    lines = ["", "ECONOMY (equipment prices, NOT money and NOT HLTV's scale - "
                 "grenades are excluded)"]
    lines.append(
        f"- Lost ${prof['value_lost']} of weapons across "
        f"{prof['deaths_priced']} deaths; took ${prof['value_taken']} across "
        f"{prof['kills_priced']} kills. Net ${prof['net']:+}.")
    if prof.get("armed_them"):
        for a in prof["armed_them"]:
            lines.append(f"- round {a['round']} tick {a['tick']}: lost a "
                         f"${a['lost']} weapon to a ${a['risked']} "
                         f"{a['weapon']}.")
        lines.append("- Those are one-way losses: down the round AND down the "
                     "weapon. The demo does NOT show whether the enemy picked "
                     "any of them up, so do not claim they did.")
    if prof.get("deaths_while_richer"):
        lines.append(
            f"- {prof['deaths_while_richer']} of those deaths came in rounds "
            f"where this player's side was clearly the better-equipped one. "
            f"That is the buy state where taking a duel you do not have to take "
            f"is most expensive.")
    lines.append(f"- {prof['note']}")
    return lines


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
        tags += _economy_tags(d)
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
    lines += _economy_lines(a.get("economy"))
    lines += _flash_lines(a.get("flashes"), a.get("flash_table"))
    lines += _kit_lines(a.get("kit"))
    lines += _aim_lines(a.get("aim"))
    lines += _comms_lines(a.get("comms"))

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
    match = load_match(path)
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
