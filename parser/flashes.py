#!/usr/bin/env python3
"""
CS2 Coach - who your flashes actually blinded.

`flashbang_detonate` says a flash went off. `player_blind` says who it caught
and for how long, which is the only part worth coaching. This module turns the
second into self / team / enemy with durations.

WHY THIS IS WORTH A MODULE
--------------------------
Team-flashing is the most common and most fixable utility mistake in pug CS, and
no free tool shows it. On the bundled match the POV throws the second-most
flashes of anyone and lands 11 of 24 meaningful ones on their own team - 31
blind-seconds, five of them over three seconds, most on the same teammate.

That comparison is the whole point. "You team-flash" is nagging; "you throw more
flashes than anyone bar RENAT4IK, and 46% of the meaningful ones blind your own
side against his 13%" is a fixable habit with a benchmark attached.

ONE FLASH IS NOT ONE OUTCOME
----------------------------
A single flashbang blinds everyone in front of it. At tick 43950 in the bundled
match one flash blinded an enemy who was immediately killed AND a teammate for
4.95 seconds. Scoring flashes as good-or-bad forces a wrong answer on every pop
like that, so each BLIND is classified separately and the counts are reported
side by side. Nothing here divides one by the other into a quality score.

SIDES ARE READ FROM POSITIONS, NOT FROM THE EVENT
-------------------------------------------------
player_blind carries no team for either party, and sides swap at half time, so
"same team" cannot be decided from names alone. It is resolved against the
nearest position frame instead - the same source the rest of the review uses.

WHAT IS NOT CLAIMED
-------------------
Not every enemy flash is meant to produce a kill: flashing an angle so nobody
can hold it works even if nothing dies. So the follow-up count is reported as a
count and explicitly not as a success rate - dividing by total flashes would
score good utility as failure. And a flash that blinds nobody is not recorded at
all by player_blind, so this cannot tell you about flashes that missed entirely.
"""

import bisect
import collections

# Below this a "blind" is the quarter-second wash you get from a flash at the
# edge of your screen. Counting those makes everyone look equally careless.
MEANINGFUL_SECONDS = 0.7

# Long enough that the person is not playing the game any more.
FULL_BLIND_SECONDS = 3.0

# How long after a blind a death still plausibly belongs to it. The blind's own
# duration, capped - a 5s blind that leads to a kill 4.5s later is a fair claim;
# anything past the blind is not.
FOLLOW_UP_CAP_SECONDS = 5.0


class _Sides:
    """
    Side lookup by (name, tick), against the nearest position frame.

    Frames are sampled every 32 ticks and blinds land on exact ticks, so this
    always searches rather than indexing. Sides swap at half time, which is why
    the tick matters at all.
    """

    def __init__(self, match):
        self.frames = match.get("frames") or []
        self.ticks = [f["tick"] for f in self.frames]

    def __call__(self, name, tick):
        if not self.frames:
            return None
        i = min(bisect.bisect_left(self.ticks, tick), len(self.frames) - 1)
        for j in (i, i - 1, i + 1):
            if not 0 <= j < len(self.frames):
                continue
            for p in self.frames[j].get("players") or []:
                if p.get("n") == name:
                    return p.get("t")
        return None


def classify(match, blinds=None):
    """
    Every blind, tagged self / team / enemy.

    Returns the input rows plus a `kind`, so a caller can group them however it
    likes rather than being handed a pre-baked verdict.
    """
    if blinds is None:
        blinds = ((match.get("utility") or {}).get("blinds")) or []
    side = _Sides(match)
    out = []
    for b in blinds:
        thrower, victim, tick = b.get("by"), b.get("who"), b.get("t")
        if not thrower or not victim or tick is None:
            continue
        if thrower == victim:
            kind = "self"
        else:
            a, v = side(thrower, tick), side(victim, tick)
            if a is None or v is None:
                # Someone was dead or not yet spawned at the nearest frame.
                # Unknown is its own answer - guessing "enemy" would quietly
                # flatter every player in the match.
                kind = "unknown"
            else:
                kind = "team" if a == v else "enemy"
        rec = dict(b)
        rec["kind"] = kind
        out.append(rec)
    return out


def _follow_ups(match, rows, thrower, tickrate):
    """
    Enemies who died while blinded by this player.

    Deliberately a count, not a rate. Flashing an angle so nobody can hold it
    works whether or not anything dies, so dividing this by flashes thrown would
    score good utility as failure.
    """
    kills = match.get("kills") or []
    by_victim = collections.defaultdict(list)
    for k in kills:
        if k.get("victim"):
            by_victim[k["victim"]].append(k)
    hits = []
    for r in rows:
        if r["kind"] != "enemy" or r["d"] < MEANINGFUL_SECONDS:
            continue
        window = int(min(r["d"], FOLLOW_UP_CAP_SECONDS) * tickrate)
        for k in by_victim.get(r["who"], []):
            if r["t"] <= k["tick"] <= r["t"] + window:
                hits.append({"round": r.get("r"), "tick": r["t"],
                             "enemy": r["who"], "blind": r["d"],
                             "killer": k.get("attacker")})
                break
    return hits


def profile(match, player, blinds=None):
    """
    One player's flash record.

    Counts and seconds side by side, never a single score. See the module
    docstring: one flash can blind an enemy and a teammate at once, and there is
    no honest way to fold that into one number.
    """
    rows = [r for r in classify(match, blinds) if r.get("by") == player]
    tickrate = match.get("tickrate") or 64

    counts = collections.Counter()
    seconds = collections.defaultdict(float)
    for r in rows:
        counts[r["kind"]] += 1
        if r["d"] >= MEANINGFUL_SECONDS:
            counts[r["kind"] + "_meaningful"] += 1
            # Seconds are summed over the SAME rows the counts describe.
            # Totalling every blind here instead pairs a meaningful count with
            # an all-blinds duration in one sentence - two populations, read as
            # one, and the reader has no way to tell.
            seconds[r["kind"]] += r["d"]

    heavy = sorted(
        (r for r in rows if r["kind"] == "team" and r["d"] >= FULL_BLIND_SECONDS),
        key=lambda r: -r["d"])
    victims = collections.Counter(
        r["who"] for r in rows
        if r["kind"] == "team" and r["d"] >= MEANINGFUL_SECONDS)

    enemy_m = counts["enemy_meaningful"]
    team_m = counts["team_meaningful"]
    total_m = enemy_m + team_m
    return {
        "player": player,
        "blinds": len(rows),
        "enemy": counts["enemy"], "team": counts["team"],
        "self": counts["self"], "unknown": counts["unknown"],
        "enemy_meaningful": enemy_m, "team_meaningful": team_m,
        "team_share": round(team_m / total_m, 3) if total_m else None,
        # Seconds cover the meaningful blinds only - the same rows the counts
        # above describe. See the loop.
        "enemy_seconds": round(seconds["enemy"], 1),
        "team_seconds": round(seconds["team"], 1),
        "self_seconds": round(seconds["self"], 1),
        "full_team_blinds": [
            {"round": r.get("r"), "tick": r["t"], "who": r["who"], "d": r["d"]}
            for r in heavy],
        "worst_victim": (victims.most_common(1)[0] if victims else None),
        "follow_ups": _follow_ups(match, rows, player, tickrate),
        "note": ("Counts, not a score - one flash can blind an enemy and a "
                 "teammate at the same time. follow_ups is a count and NOT a "
                 "success rate: a flash that stops an angle being held worked "
                 "whether or not anything died. Flashes that caught nobody are "
                 "not recorded at all."),
    }


def match_table(match, blinds=None):
    """
    Every player's flash record, so one player's number has something to mean.

    Without the other nine, "46% of your flashes hit your own team" is a number
    with no scale attached.
    """
    rows = classify(match, blinds)
    names = sorted({r["by"] for r in rows})
    table = {}
    for n in names:
        p = profile(match, n, blinds)
        table[n] = {k: p[k] for k in
                    ("enemy_meaningful", "team_meaningful", "team_share",
                     "enemy_seconds", "team_seconds")}
    return table


def main():
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: flashes.py data/match.json [player]")
        return 1
    match = json.load(open(sys.argv[1], encoding="utf-8"))
    player = sys.argv[2] if len(sys.argv) > 2 else match.get("pov")

    blinds = (match.get("utility") or {}).get("blinds")
    if not blinds:
        print("This match has no blind data. Re-parse the demo - player_blind "
              "was added after it was last parsed.")
        return 1

    print(f"FLASHES (blinds of {MEANINGFUL_SECONDS}s or more)\n")
    print(f"  {'player':<14} {'enemy':>6} {'team':>6} {'team %':>7} "
          f"{'enemy s':>8} {'team s':>7}")
    for name, t in sorted(match_table(match, blinds).items(),
                          key=lambda kv: -(kv[1]["team_share"] or 0)):
        share = "-" if t["team_share"] is None else f"{t['team_share']*100:.0f}%"
        print(f"  {name:<14} {t['enemy_meaningful']:>6} "
              f"{t['team_meaningful']:>6} {share:>7} "
              f"{t['enemy_seconds']:>8} {t['team_seconds']:>7}")

    p = profile(match, player, blinds)
    print(f"\n{player}: {p['team_meaningful']} of "
          f"{p['team_meaningful'] + p['enemy_meaningful']} meaningful flashes "
          f"blinded a teammate ({p['team_seconds']}s of team blindness).")
    if p["worst_victim"]:
        who, n = p["worst_victim"]
        print(f"  most often: {who}, {n} times")
    for h in p["full_team_blinds"]:
        print(f"  round {h['round']}: {h['who']} blinded {h['d']}s")
    if p["follow_ups"]:
        print(f"\n  {len(p['follow_ups'])} enemies died while blinded by you:")
        for h in p["follow_ups"]:
            print(f"    round {h['round']}: {h['enemy']} ({h['blind']}s) "
                  f"killed by {h['killer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
