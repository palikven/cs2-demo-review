#!/usr/bin/env python3
"""
CS2 Coach - role evidence.

A FACEIT pug has no roles. A team match does, and the same death means opposite
things depending on which one you were playing: an entry fragger dying first at
5v5 did their job if someone traded them, and an anchor dying first at 5v5 threw
the round. Coaching both with one rulebook produces advice that is wrong for
everyone.

THIS MODULE DOES NOT ASSIGN ROLES
---------------------------------
It reports what the demo can show about each player and stops there. The
temptation is to infer - most opening kills is the entry, most AWP rounds is the
AWPer - and on one match the sample will not carry it. A 22-round match contains
22 opening duels shared across ten players: 2.2 each. The bundled demo's top
"entry" has five opening kills, which is a coin landing heads five times. The
top AWP holder carried one in 4 of 22 rounds, which is someone who picked it up
when they could afford it, not an AWPer.

So: the user assigns roles, which is what they asked for, and this puts the
demo's evidence next to each name so the pick is informed rather than blind.
`suggest()` exists and is deliberately conservative - it returns a role only
where the evidence is lopsided enough that a human would not disagree, and
returns None for everybody in an ordinary pug.

WHAT COUNTS AS EVIDENCE
-----------------------
Only things already in the parse:

  awp_rounds       rounds where they held an AWP at any sampled frame
  opening_kills    first player kill of a round, by them
  opening_deaths   first player kill of a round, against them
  nades            grenades thrown (from the utility export)
  flashes/smokes   the two that indicate a support pattern rather than a
                   frag-oriented one
  median_spacing   distance to the nearest living teammate when they died

Everything here is a count over one match, so every number is reported with its
denominator and nothing is described as a tendency.
"""

import collections
import statistics

# A role is only suggested when the evidence is lopsided. These are deliberately
# hard to reach on a single match - the point is to stay quiet in a pug.
#
# The rounds floor comes first and applies to all of them. Every other threshold
# here is a ratio, and ratios are cheapest to satisfy on the smallest sample: on
# a five-round slice one AWP round is 20% of the match and a single eco where
# one player bought the nades is 2x the team average. Half a standard MR12 match
# is the least that can distinguish a role from a round.
MIN_ROUNDS_FOR_SUGGESTION = 12

MIN_AWP_ROUND_FRACTION = 0.5      # half the match with an AWP is an AWPer
MIN_OPENING_SHARE = 0.35          # a third of the team's opening duels
MIN_OPENING_DUELS = 6             # ...and enough of them to mean anything
MIN_UTILITY_SHARE = 1.6           # 60% more grenades than the team average

ROLES = ("igl", "awper", "entry", "support", "anchor", "rifle")

# What actually changes in the coaching. Every entry names the fields the
# judgement rests on and, just as importantly, what NOT to hold against them -
# a rulebook that only adds priorities makes every role sound like a fragger
# with extra homework.
ROLE_GUIDANCE = {
    "entry": (
        "This player is the ENTRY. Their job is to take space first and create "
        "a trade, so judge them on their opening-duel record and on whether "
        "their opening deaths were TRADED - both are in the sheet. An untraded "
        "opening death is as much the team's failure to follow as it is theirs; "
        "say so rather than blaming the entry for dying first. Do not coach "
        "them toward survival, K/D or staying alive longer: an entry who stops "
        "entering is not doing the job. Closing ground into a duel is expected "
        "of them, so do not treat it as a mistake by itself."
    ),
    "awper": (
        "This player is the AWPER. Judge them on picks taken at range and on "
        "whether they repositioned after taking one - deaths carry the range "
        "and how much ground each side closed. Dying with an AWP costs the "
        "team the round's most expensive gun, so a death while THEY were "
        "closing ground is a heavier mistake than the same death with a rifle; "
        "the sheet says who closed. Do not judge them on entry duels, trade "
        "participation or utility thrown - none of those are their job."
    ),
    "support": (
        "This player is SUPPORT. Judge them on two things the sheet carries: "
        "how close they were to the nearest living teammate when duels "
        "happened (support that is out of trade range is not support), and "
        "whether their deaths were traded. Do not judge them on opening duels, "
        "kill counts or rating - a support with a 0.9 rating whose team wins "
        "the rounds they set up is doing the job. Flashes ARE judgeable and "
        "matter most for this role: the sheet says who each of their flashes "
        "blinded and for how long, with everyone else's team-flash share for "
        "scale - a support who blinds their own entry is actively costing "
        "rounds. Smokes and molotovs are not: the demo carries counts and "
        "positions but nothing about whether one landed usefully, so do not "
        "claim it did or did not."
    ),
    "anchor": (
        "This player is an ANCHOR holding a site alone on CT. Their job is "
        "delay and information, not kills. The number that matters is how far "
        "into the round they died - it is on every death line in seconds. "
        "Dying at 1v3 forty seconds in having given the team a rotation is a "
        "job done; dying ten seconds in at 5v5 is the round. Do not judge them "
        "on being isolated or on nearest-teammate distance: holding alone is "
        "the assignment, and the isolation numbers in the sheet mean something "
        "different for them than for anyone else."
    ),
    "igl": (
        "This player is the IGL. Weight round outcomes and round swing above "
        "their individual stats - an IGL at 0.95 rating whose rounds are won "
        "is doing the job, and telling them to frag more is usually advice to "
        "stop calling. The sheet has per-round results and this player's swing "
        "per round; lead with those. Their positioning still matters, so "
        "individual deaths are fair to review - just do not build the review "
        "around their rating or K/D."
    ),
    "rifle": (
        "This player is a RIFLER with no specialist assignment. The general "
        "review applies: trades, spacing, where they repeatedly die, and how "
        "they entered the duels they lost. No role-specific exemptions."
    ),
}


def teams(match):
    """
    name -> team id (0 or 1), from who started on which side.

    Sides swap at the half, so `side` in any given frame is not team identity.
    Round one is: whoever shared a side then are teammates for the match.

    A player who joins after round one is placed by overlap - at any frame they
    appear in, the people on their side are their team, and whichever round-one
    group those mostly belong to is theirs. Without this a substitute lands in
    whichever team happened to be CT when they arrived.
    """
    frames = match.get("frames") or []
    if not frames:
        return {}
    first = frames[0]
    team = {}
    for p in first.get("players") or []:
        side = (p.get("t") or "").lower()
        if side in ("t", "ct"):
            team[p["n"]] = 0 if side == "t" else 1

    known = set(team)
    for f in frames:
        missing = [p for p in f.get("players") or [] if p["n"] not in team]
        if not missing:
            continue
        for p in missing:
            same_side = [q["n"] for q in f["players"]
                         if q["n"] != p["n"] and q.get("t") == p.get("t")
                         and q["n"] in known]
            if not same_side:
                continue
            votes = collections.Counter(team[n] for n in same_side)
            team[p["n"]] = votes.most_common(1)[0][0]
    return team


def _awp_rounds(match):
    """Rounds in which each player held an AWP at any sampled frame."""
    names = match.get("item_names") or []
    loadouts = match.get("loadouts") or []
    awp_items = {i for i, n in enumerate(names) if n == "AWP"}
    if not awp_items:
        return {}
    awp_loadouts = {i for i, l in enumerate(loadouts) if awp_items & set(l)}
    held = collections.defaultdict(set)
    for f in match.get("frames") or []:
        rnd = f.get("round")
        for p in f.get("players") or []:
            if p.get("eq") in awp_loadouts:
                held[p["n"]].add(rnd)
    return {n: len(rs) for n, rs in held.items()}


def _opening_duels(match):
    """Who won and who lost the first player kill of each round."""
    by_round = collections.defaultdict(list)
    for k in match.get("kills") or []:
        if k.get("attacker") and k["attacker"] != k.get("victim"):
            by_round[k.get("round")].append(k)
    won, lost = collections.Counter(), collections.Counter()
    for kills in by_round.values():
        first = min(kills, key=lambda k: k["tick"])
        won[first["attacker"]] += 1
        lost[first["victim"]] += 1
    return won, lost, len(by_round)


def _utility(match):
    """Grenades thrown per player, and the two kinds that indicate support."""
    util = match.get("utility") or {}
    thrown = collections.Counter(t["by"] for t in util.get("throws") or []
                                 if t.get("by"))
    flashes = collections.Counter(e["by"] for e in util.get("flashes") or []
                                  if e.get("by"))
    smokes = collections.Counter(e["by"] for e in util.get("smokes") or []
                                 if e.get("by"))
    return thrown, flashes, smokes


def evidence(match):
    """
    Per-player role evidence, every count carrying its denominator.

    Denominators are the whole point. "5 opening kills" invites a conclusion;
    "5 of the 22 opening duels in this match" invites the right one.
    """
    rounds = ((match.get("stats") or {}).get("rounds_played")
              or len(match.get("rounds") or []) or 0)
    awp = _awp_rounds(match)
    won, lost, total_openings = _opening_duels(match)
    thrown, flashes, smokes = _utility(match)

    names = [p.get("name") for p in match.get("players") or [] if p.get("name")]
    # Against their OWN team, not the lobby. Utility economy is a team property:
    # a team that saves differently throws differently, and averaging across
    # both sides moves the bar by enough to change the answer. On the bundled
    # match the lobby average is 35 and the POV's team's is 39, which is the
    # difference between suggesting they are the support and not.
    side_of = teams(match)
    out = {}
    for n in names:
        mates = [m for m in names if side_of.get(m) == side_of.get(n)] or [n]
        avg = statistics.mean([thrown[m] for m in mates])
        out[n] = {
            "rounds": rounds,
            "awp_rounds": awp.get(n, 0),
            "opening_kills": won[n],
            "opening_deaths": lost[n],
            "opening_duels_in_match": total_openings,
            "nades": thrown[n],
            "flashes": flashes[n],
            "smokes": smokes[n],
            "team_average_nades": round(avg, 1),
            "teammates": sorted(m for m in mates if m != n),
        }
    return out


def suggest(ev):
    """
    A role, only where one player's evidence is lopsided enough to be obvious.

    Returns None far more often than not, and that is correct behaviour on a
    pug. A suggestion the user has to undo is worse than no suggestion: it
    arrives looking like a measurement.

    IGL is never suggested. Nothing in a demo distinguishes the caller - it is
    in the comms, and even with voice extracted, talk time is not leadership.
    """
    rounds = ev.get("rounds") or 0
    if rounds < MIN_ROUNDS_FOR_SUGGESTION:
        return None
    if ev.get("awp_rounds", 0) >= MIN_AWP_ROUND_FRACTION * rounds:
        return "awper"

    total = ev.get("opening_duels_in_match") or 0
    duels = ev.get("opening_kills", 0) + ev.get("opening_deaths", 0)
    if (total and duels >= MIN_OPENING_DUELS
            and duels >= MIN_OPENING_SHARE * total):
        return "entry"

    avg = ev.get("team_average_nades") or 0
    if avg and ev.get("nades", 0) >= MIN_UTILITY_SHARE * avg:
        return "support"
    return None


def describe(name, ev):
    """One line of evidence for the role picker, in the user's language."""
    bits = [f"{ev['opening_kills']}W/{ev['opening_deaths']}L of the match's "
            f"{ev['opening_duels_in_match']} opening duels"]
    if ev["awp_rounds"]:
        bits.append(f"AWP in {ev['awp_rounds']}/{ev['rounds']} rounds")
    bits.append(f"{ev['nades']} nades thrown (team average "
                f"{ev['team_average_nades']:.0f})")
    return f"{name}: " + ", ".join(bits)


def guidance(role):
    """The coaching rules for a role, or None. Unknown roles change nothing."""
    return ROLE_GUIDANCE.get((role or "").strip().lower()) or None


def main():
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: roles.py data/match.json")
        return 1
    match = json.load(open(sys.argv[1], encoding="utf-8"))
    ev = evidence(match)
    print("Role evidence. Nothing here assigns a role - assign them yourself.\n")
    for name, e in sorted(ev.items(), key=lambda kv: -kv[1]["opening_kills"]):
        s = suggest(e)
        print(f"  {describe(name, e)}"
              + (f"\n      -> evidence is lopsided enough to suggest: {s}" if s
                 else ""))
    if not any(suggest(e) for e in ev.values()):
        print("\nNo player's evidence is lopsided enough to suggest a role. "
              "That is the normal answer for a pug.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
