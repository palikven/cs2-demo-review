#!/usr/bin/env python3
"""
CS2 Coach - what happens as a spray goes on.

`weapon_fire` says a shot happened. `fire_bullets` says where it left from, the
angle it left on, and - the part nothing else exposes - `recoil_index`: which
bullet of the burst it was.

That last field is the whole module. Hit rate by depth into the spray is a
habit you can actually change, and on the bundled match it falls off a cliff:

    bullet 1st    65 fired   14 hit   21.5%
    bullet 2-4   149 fired   42 hit   28.2%
    bullet 5-9   123 fired   19 hit   15.4%
    bullet 10+    68 fired    6 hit    8.8%

Sixty-eight bullets past the tenth for six hits. "Stop spraying" is advice
everyone has heard; "you fired 68 bullets past the tenth this match and hit six"
is a number.

WHAT A HIT MEANS HERE
---------------------
A bullet is marked as connecting when `player_hurt` records damage from that
player on that same tick. A shotgun puts several pellets on one tick and
produces several hurt rows, so this counts the SHOT as connecting rather than
counting pellets - which is the question anyone actually asks, and it means
shotgun accuracy is not comparable with rifle accuracy. Both are reported
against the rest of the server rather than against an absolute, which is the
only comparison that survives that.

WHAT IT CANNOT SEE
------------------
Whether there was anything to hit. A player spraying through a smoke to deny a
push is doing something useful and scores zero here, and the demo cannot tell
that apart from bad trigger discipline. So the tail is reported as a count with
its hit rate beside it, never as a verdict, and the note says so.

Nor does it know whether a miss was a bad angle, a bad crosshair or a whiff.
recoil_index says how deep into the spray they were, not why they missed.
"""

import collections

# Buckets, not raw indices. Per-index hit rates on one match are three or four
# bullets each and read as noise; these are the shapes people recognise - the
# tap, the burst, the spray, and the part after the pattern has gone.
BUCKETS = (("1st", 0, 1), ("2-4", 1, 4), ("5-9", 4, 9), ("10+", 9, 10 ** 6))

# Below this many bullets a hit rate is not worth printing, let alone coaching.
MIN_BULLETS = 25

# Where "the tail" starts - past this, the pattern is gone for every rifle in
# the game and holding the trigger is a choice rather than a spray.
TAIL_FROM = 9


def bucket_of(index):
    for name, lo, hi in BUCKETS:
        if lo <= index < hi:
            return name
    return BUCKETS[-1][0]


def by_player(match):
    """{player: {bucket: [fired, hit]}} over every bullet in the match."""
    out = collections.defaultdict(
        lambda: collections.defaultdict(lambda: [0, 0]))
    for s in (match.get("utility") or {}).get("shots") or []:
        who = s.get("by")
        if not who:
            continue
        row = out[who][bucket_of(int(s.get("i") or 0))]
        row[0] += 1
        if s.get("h"):
            row[1] += 1
    return out


def _rate(hit, fired):
    return round(hit / fired, 3) if fired else None


def match_table(match):
    """
    Every shooter's overall hit rate and their tail.

    Ships beside the profile for the same reason flashes.py and kit.py do: a
    20% hit rate means nothing until you know the other nine are at 18% or 34%.
    """
    table = {}
    for name, buckets in by_player(match).items():
        fired = sum(b[0] for b in buckets.values())
        hit = sum(b[1] for b in buckets.values())
        if fired < MIN_BULLETS:
            continue
        tail = buckets.get("10+", [0, 0])
        table[name] = {
            "fired": fired, "hit": hit, "rate": _rate(hit, fired),
            "tail_fired": tail[0], "tail_hit": tail[1],
            "tail_rate": _rate(tail[1], tail[0]),
            "tail_share": _rate(tail[0], fired),
        }
    return table


def profile(match, player):
    """One player's spray record, with the match around it."""
    buckets = by_player(match).get(player)
    if not buckets:
        return {}
    fired = sum(b[0] for b in buckets.values())
    if fired < MIN_BULLETS:
        return {}
    hit = sum(b[1] for b in buckets.values())

    rows = []
    for name, _, _ in BUCKETS:
        f, h = buckets.get(name, [0, 0])
        rows.append({"bucket": name, "fired": f, "hit": h,
                     "rate": _rate(h, f)})

    table = match_table(match)
    ranked = sorted(table.items(), key=lambda kv: -(kv[1]["rate"] or 0))
    rank = next((i + 1 for i, (n, _) in enumerate(ranked) if n == player), None)
    tail = buckets.get("10+", [0, 0])

    return {
        "player": player,
        "fired": fired, "hit": hit, "rate": _rate(hit, fired),
        "rank": rank, "of": len(ranked),
        "buckets": rows,
        "tail_fired": tail[0], "tail_hit": tail[1],
        "tail_rate": _rate(tail[1], tail[0]),
        "table": {n: t["rate"] for n, t in ranked},
        "note": ("A bullet counts as connecting when player_hurt records damage "
                 "from that player on the same tick, so a shotgun's pellets "
                 "count once and shotgun accuracy is not comparable with rifle "
                 "accuracy - use the rank against the other players, not the "
                 "absolute. This cannot see whether there was anything to hit: "
                 "spraying a smoke to deny a push scores zero and is good play. "
                 "Report the tail as a count, never as a verdict."),
    }


def main():
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: spray.py data/match.json [player]")
        return 1
    match = json.load(open(sys.argv[1], encoding="utf-8"))
    player = sys.argv[2] if len(sys.argv) > 2 else match.get("pov")

    if not (match.get("utility") or {}).get("shots"):
        print("No bullet data. Re-parse the demo - fire_bullets was added "
              "after it was last parsed.")
        return 1

    print("HIT RATE BY DEPTH INTO THE SPRAY\n")
    print(f"  {'player':<14}{'fired':>7}{'hit':>6}{'rate':>7}"
          f"{'tail':>7}{'tail hit':>10}")
    for name, t in sorted(match_table(match).items(),
                          key=lambda kv: -(kv[1]["rate"] or 0)):
        tr = "-" if t["tail_rate"] is None else f"{t['tail_rate'] * 100:.0f}%"
        mark = "  <- you" if name == player else ""
        print(f"  {name:<14}{t['fired']:>7}{t['hit']:>6}"
              f"{t['rate'] * 100:>6.0f}%{t['tail_fired']:>7}{tr:>10}{mark}")

    p = profile(match, player)
    if not p:
        return 0
    print(f"\n{player}: {p['hit']}/{p['fired']} ({p['rate'] * 100:.0f}%), "
          f"rank {p['rank']} of {p['of']}\n")
    for b in p["buckets"]:
        if not b["fired"]:
            continue
        print(f"  bullet {b['bucket']:<4} {b['fired']:>4} fired  "
              f"{b['hit']:>3} hit  {b['rate'] * 100:>5.1f}%")
    if p["tail_fired"]:
        print(f"\n  {p['tail_fired']} bullets past the {TAIL_FROM + 1}th for "
              f"{p['tail_hit']} hits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
