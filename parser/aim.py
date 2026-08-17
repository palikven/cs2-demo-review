#!/usr/bin/env python3
"""
CS2 Coach - where your crosshair sits while you move.

Source convention: pitch is degrees, POSITIVE is looking DOWN, 0 is level. A
player moving through the map with their crosshair at head height reads near 0;
one staring at the floor reads 6-10 and has to flick up before every duel.

On the bundled match the medians run from -0.3 to +7.1 while moving, which is a
real and visible spread, not noise.

WHY THIS IS NOT SIMPLY "LOW PITCH IS BAD"
-----------------------------------------
Looking down is CORRECT on a staircase, a ramp, or a drop. Anubis has plenty of
all three. Pitch on its own therefore measures the map as much as the player.

What makes it coachable is that all ten players walk the same corridors, so map
geometry is common-mode and cancels in a ranking. But only partly: T and CT
walk different halves of the map, and players hold different positions.

So the claim is gated on holding CLEARLY across BOTH sides - see
MARGIN_DEGREES. A player well above the match median as T and as CT is doing
something the map cannot explain, because they played both halves of it. A
player high on one side only is probably describing a position, and this says so
instead of coaching them.

On the bundled match exactly one of ten clears that bar, and the POV is not it:
they are above the median as T and below it as CT, so the module refuses to
coach them. A gate that never fires and a gate that always fires are both
useless, so it is worth knowing this one discriminates.

WHAT IS SAMPLED, AND WHY
------------------------
Only frames where the player is alive, actually moving, and was not shot in the
last second and a half:

  * ALIVE, because a dying player's view swings hard - the bundled match has a
    death where pitch runs 1.4 -> 9.9 degrees over the last second purely from
    taking damage.
  * MOVING, because standing still includes holding an angle deliberately aimed
    at a doorway on the floor below, which is good play, and idling in spawn.
  * NOT RECENTLY SHOT, for the same reason as the first. This one was measured
    rather than assumed: excluding it moves every median by at most 0.2 degrees
    and changes no rankings, because only ~2% of frames qualify. It is kept
    because it is correct and free, not because it mattered.

WHAT THIS IS NOT
----------------
It is not crosshair PLACEMENT in the full sense - that is about holding the
angle a head will appear at, which needs to know where the enemy was about to
be. It is the vertical half of it, measured while walking, which is the half the
demo can prove. Do not let it become "your crosshair placement is bad".
"""

import bisect
import collections
import math
import statistics

# Below this the player is standing still or shuffling - see the docstring.
MOVING_UNITS_PER_SEC = 40

# Frames within this of taking damage are dropped; a hit swings the view.
HURT_WINDOW_TICKS = 96          # 1.5s at 64-tick

# Two consecutive samples further apart than this are not a continuous motion -
# usually a round boundary or a respawn.
MAX_GAP_SECONDS = 1.0

# Fewer samples than this on a side and the median is not worth reporting.
MIN_SAMPLES_PER_SIDE = 200

# How far above the match median counts as a habit rather than a rounding
# difference. "Above the median" alone fires for half the server by
# construction - on the bundled match it passed a player sitting +0.4 degrees
# over, which is nothing. At a 500-unit engagement 1.5 degrees is about 13
# units of drop, roughly a head, which is the point at which "your crosshair is
# below head level" starts describing something a person could see.
MARGIN_DEGREES = 1.5


def _hurt_index(match):
    by_victim = collections.defaultdict(list)
    for d in match.get("damages") or []:
        if d.get("v") is not None and d.get("t") is not None:
            by_victim[d["v"]].append(d["t"])
    for v in by_victim.values():
        v.sort()
    return by_victim


def samples(match):
    """
    {player: {side: [pitch, ...]}} over the frames worth measuring.

    One pass over the frames, tracking each player's previous position so speed
    can be derived - the frames carry no velocity.
    """
    hurt = _hurt_index(match)
    tickrate = match.get("tickrate") or 64
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    prev = {}

    for f in match.get("frames") or []:
        tick = f.get("tick")
        if tick is None:
            continue
        for p in f.get("players") or []:
            name, pitch = p.get("n"), p.get("pi")
            if not name:
                continue
            last = prev.get(name)
            if p.get("x") is not None:
                prev[name] = (tick, p["x"], p["y"])
            if pitch is None or last is None:
                continue
            if p.get("a") is False or (p.get("hp") or 0) <= 0:
                continue
            dt = (tick - last[0]) / tickrate
            if dt <= 0 or dt > MAX_GAP_SECONDS:
                continue
            speed = math.hypot(p["x"] - last[1], p["y"] - last[2]) / dt
            if speed < MOVING_UNITS_PER_SEC:
                continue
            shots = hurt.get(name)
            if shots:
                i = bisect.bisect_right(shots, tick) - 1
                if i >= 0 and tick - shots[i] <= HURT_WINDOW_TICKS:
                    continue
            side = (p.get("t") or "?").lower()
            out[name][side].append(pitch)
    return out


def match_table(match):
    """Every player's median pitch while moving, overall and per side."""
    data = samples(match)
    table = {}
    for name, sides in data.items():
        allv = [v for vs in sides.values() for v in vs]
        if not allv:
            continue
        row = {"n": len(allv), "median": round(statistics.median(allv), 1)}
        for side in ("t", "ct"):
            vals = sides.get(side) or []
            row[side] = (round(statistics.median(vals), 1)
                         if len(vals) >= MIN_SAMPLES_PER_SIDE else None)
            row[f"n_{side}"] = len(vals)
        table[name] = row
    return table


def profile(match, player):
    """
    One player, against the match.

    `both_sides` is the gate: only when a player is clearly above the match
    median on T AND on CT is the reading about them rather than about where
    they stood. Clearly, not merely - see MARGIN_DEGREES.
    """
    table = match_table(match)
    mine = table.get(player)
    if not mine:
        return {}

    ranked = sorted(table.items(), key=lambda kv: kv[1]["median"])
    rank = next((i + 1 for i, (n, _) in enumerate(ranked) if n == player), None)
    match_median = statistics.median([r["median"] for r in table.values()])

    above = {}
    for side in ("t", "ct"):
        vals = [r[side] for r in table.values() if r.get(side) is not None]
        if mine.get(side) is None or len(vals) < 2:
            above[side] = None
        else:
            above[side] = mine[side] > statistics.median(vals) + MARGIN_DEGREES
    both = above["t"] is True and above["ct"] is True

    return {
        "player": player,
        "median": mine["median"],
        "t": mine.get("t"), "ct": mine.get("ct"),
        "samples": mine["n"],
        "rank": rank, "of": len(ranked),
        "match_median": round(match_median, 1),
        "above_on_t": above["t"], "above_on_ct": above["ct"],
        "both_sides": both,
        "table": {n: r["median"] for n, r in ranked},
        "claimable": bool(both),
        "note": ("Degrees; POSITIVE is looking DOWN, 0 is level. Looking down "
                 "is correct on stairs and ramps, so this only says something "
                 "about the PLAYER when they are clearly above the match "
                 "median on BOTH sides - they walked both halves of the same "
                 "map. If "
                 "`claimable` is false, say the number and say it may just be "
                 "where they played; do not coach it. This is the vertical "
                 "half of crosshair placement measured while walking, not "
                 "crosshair placement in full - never call it that."),
    }


def main():
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: aim.py data/match.json [player]")
        return 1
    match = json.load(open(sys.argv[1], encoding="utf-8"))
    player = sys.argv[2] if len(sys.argv) > 2 else match.get("pov")

    table = match_table(match)
    if not table:
        print("No pitch data in this match. Re-parse the demo - `pitch` was "
              "added to the frame export after it was last parsed.")
        return 1

    print("CROSSHAIR HEIGHT WHILE MOVING (degrees, + is looking DOWN)\n")
    print(f"  {'player':<14}{'median':>8}{'T':>7}{'CT':>7}{'samples':>9}")
    for name, r in sorted(table.items(), key=lambda kv: -kv[1]["median"]):
        t = "-" if r["t"] is None else f"{r['t']:.1f}"
        c = "-" if r["ct"] is None else f"{r['ct']:.1f}"
        mark = "  <- you" if name == player else ""
        print(f"  {name:<14}{r['median']:>8.1f}{t:>7}{c:>7}{r['n']:>9}{mark}")

    p = profile(match, player)
    if not p:
        return 0
    print(f"\n{player}: median {p['median']}deg, rank {p['rank']} of {p['of']} "
          f"(match median {p['match_median']}deg) - lower is better.")
    if p["claimable"]:
        print(f"  Above the match median on BOTH sides (T {p['t']}, CT "
              f"{p['ct']}), so this is about them, not about where they stood.")
    else:
        print(f"  NOT above the median on both sides (T {p['t']}, CT {p['ct']}) "
              f"- this may just be the positions they played. Not coachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
