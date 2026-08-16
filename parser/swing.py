#!/usr/bin/env python3
"""
CS2 Coach - round win probability and swing

WHAT THIS IS NOT
----------------
This is not HLTV's Round Swing. HLTV introduced Rating 3.0 in August 2025 with
Round Swing as its largest component (~33% of the rating), but they publish the
concept and the component weights, not the model: the win-probability surface
behind it is fitted against their entire match database and is proprietary.

So this is OUR model of the same idea, with every coefficient written down
below and a calibration report you can run against your own demos. It will not
reproduce HLTV's numbers and is not meant to. Where a defensible published
figure is wanted, use Rating 1.0 in parse_demo.py.

WHAT IT DOES
------------
For any moment in a round we estimate P(the T side wins this round) from the
state the demo actually records:

    * how many players are alive on each side      - the dominant term
    * whether the bomb is down                     - flips the burden of action
    * the equipment each side is holding           - a rifle beats a pistol
    * which side tends to win on this map          - taken from the match itself

Swing for a kill is then simply how much that probability moved:

    swing = P(win | after the kill) - P(win | before)

from the acting player's own side's point of view. A kill that takes a 5v5 to a
5v4 is worth far less than the one that takes a 2v2 to a 2v1, which is the
entire point - it measures what the kill was worth, not that it happened.

CALIBRATION
-----------
A model nobody checks is a decoration. `calibrate()` bins every round state by
predicted probability and reports what actually happened, plus a Brier score.
Run it on your own demos: one match is 22 outcomes and cannot validate
anything, but the numbers are printed rather than assumed, and they improve as
you add matches.
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# ---------------------------------------------------------------------------
# The model. Every number here is a coefficient in a logistic regression on
# the T side's log-odds of winning the round. They are stated, not fitted -
# see the module docstring - and chosen to reproduce the round-win rates that
# are well established for Counter-Strike at each man advantage.
# ---------------------------------------------------------------------------

# Each extra living player is worth this much in log-odds. 0.95 puts a one-man
# advantage at ~72% and a two-man at ~87%, which is about where CS has always
# sat. This is by far the largest term and the one worth getting right.
B_MAN = 0.95

# ...but a man advantage is not worth the same at every stage of a round. Going
# 2v2 to 2v1 decides it; going 5v5 to 5v4 barely dents it. A model that reads
# only (alive_t - alive_ct) scores those two identically, which makes swing a
# kill count with extra steps - the exact thing the metric exists to avoid.
#
# So the advantage is scaled by how few players are left. Normalised to 1.0 at
# 5v5 so B_MAN keeps its meaning, and capped because the last-man states are
# already near the clamps and do not need further amplification.
REFERENCE_ALIVE = 10.0
MAX_SCARCITY = 2.0


def scarcity(total_alive):
    """How much a single body is worth, relative to a 5v5."""
    if total_alive <= 0:
        return MAX_SCARCITY
    return min(MAX_SCARCITY, math.sqrt(REFERENCE_ALIVE / total_alive))

# A planted bomb moves the burden onto the CTs: they now have to retake and
# defuse against the clock. ~0.6 puts an even-man post-plant at ~65% for the T
# side.
B_BOMB = 0.60

# Equipment, measured in full-buy equivalents (3700 = rifle + kevlar/helmet).
# A side holding rifles against pistols is roughly a one-man advantage, so this
# is deliberately smaller than B_MAN: gear matters, bodies matter more.
B_EQUIP = 0.55

# Everything is clamped away from certainty. A 5v1 is not a guaranteed win and
# a model that says 0.999 makes every subsequent event look worthless.
P_FLOOR, P_CEIL = 0.02, 0.98

FULL_BUY = 3700


def _logit(p):
    return math.log(p / (1 - p))


def _sigmoid(x):
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1 / (1 + math.exp(-x))


def win_probability(alive_t, alive_ct, bomb_planted=False,
                    equip_t=0.0, equip_ct=0.0, t_baseline=0.5):
    """
    P(the T side wins the round) for one moment.

    equip_* are totals in dollars for the living players on that side;
    t_baseline is the T side's underlying win rate on this map, which the
    caller takes from the match rather than assuming 50/50.
    """
    if alive_t <= 0 and alive_ct <= 0:
        return 0.5
    # A side with nobody left cannot win, bomb or not. The one exception is a
    # planted bomb with the last T dead, which the CTs still have to defuse -
    # the demo settles that within seconds, so we let the round result stand
    # rather than modelling it.
    if alive_t <= 0:
        return P_FLOOR if not bomb_planted else 0.25
    if alive_ct <= 0:
        return P_CEIL

    x = _logit(min(max(t_baseline, 0.15), 0.85))
    x += B_MAN * (alive_t - alive_ct) * scarcity(alive_t + alive_ct)
    if bomb_planted:
        x += B_BOMB
    if alive_t and alive_ct:
        per_t = equip_t / alive_t / FULL_BUY
        per_ct = equip_ct / alive_ct / FULL_BUY
        x += B_EQUIP * (per_t - per_ct)
    return min(max(_sigmoid(x), P_FLOOR), P_CEIL)


# ---------------------------------------------------------------------------
# Reading state out of a parsed match
# ---------------------------------------------------------------------------

def side_baseline(match):
    """
    The T side's round win rate in this match, used as the model's starting
    point instead of a flat 50/50.

    Falls back to 0.5 on a short match - twenty rounds is a thin basis for a
    map baseline, and pretending otherwise would bake one team's good half into
    every probability.
    """
    rounds = match.get("rounds") or []
    wins = [r.get("winner") for r in rounds if r.get("winner")]
    if len(wins) < 10:
        return 0.5
    return sum(1 for w in wins if str(w).lower().startswith("t")) / len(wins)


def _equip_of(match, player):
    """Weapon price + armour for one player entry in a frame."""
    if player.get("hp", 100) <= 0:
        return 0
    vals = match.get("loadout_values") or []
    prices = match.get("armor_prices") or {}
    wep = vals[player["eq"]] if player.get("eq") is not None and player["eq"] < len(vals) else 0
    kit = 0
    if player.get("ar"):
        kit = prices.get("vest", 650) + (prices.get("helmet_extra", 350)
                                         if player.get("hm") else 0)
    return wep + kit


def state_at(match, frame, bomb_planted):
    """(alive_t, alive_ct, equip_t, equip_ct) from one sampled frame."""
    at = act = et = ect = 0
    for p in frame.get("players") or []:
        if (p.get("hp") or 0) <= 0:
            continue
        side = p.get("t")
        val = _equip_of(match, p)
        if side == "t":
            at += 1
            et += val
        elif side == "ct":
            act += 1
            ect += val
    return at, act, et, ect


def bomb_planted_at(match, rnd, tick):
    for b in match.get("bomb") or []:
        if b.get("r") == rnd and b.get("event") == "plant" and b.get("t", 0) <= tick:
            return True
    return False


def live_windows(match):
    """
    (first_tick, last_tick) of actual play for each round.

    A round's `start`..`official_end` also covers freeze time and the
    post-round period, and during those the demo still reports the PREVIOUS
    round's bodies - round 5 of the sample match opens 3v5 because nobody has
    respawned yet. Reading a win probability off that is meaningless, so
    anything probability-related is clipped to freeze_end..end.
    """
    out = {}
    for r in match.get("rounds") or []:
        n = r.get("round_num")
        if n is None:
            continue
        start = r.get("freeze_end") or r.get("start")
        end = r.get("end") or r.get("official_end")
        if start is not None and end is not None:
            out[n] = (start, end)
    return out


def frames_by_round(match, live_only=False):
    windows = live_windows(match) if live_only else {}
    out = defaultdict(list)
    for f in match.get("frames") or []:
        r = f.get("round")
        if r is None:
            continue
        if live_only:
            w = windows.get(r)
            if w and not (w[0] <= f["tick"] <= w[1]):
                continue
        out[r].append(f)
    for r in out:
        out[r].sort(key=lambda f: f["tick"])
    return out


def nearest_frame(frames, tick):
    if not frames:
        return None
    best, bd = None, None
    for f in frames:
        d = abs(f["tick"] - tick)
        if bd is None or d < bd:
            best, bd = f, d
    return best


# ---------------------------------------------------------------------------
# Swing per kill
# ---------------------------------------------------------------------------

def compute_swing(match):
    """
    Swing for every kill in the match.

    Each kill's swing is measured by removing the victim from the state just
    before it - rather than by diffing two sampled frames, which at 2Hz would
    fold in whatever else happened in that half second and attribute it to
    whoever happened to get the kill.
    """
    base = side_baseline(match)
    by_round = frames_by_round(match)
    out = []

    for k in match.get("kills") or []:
        rnd, tick = k.get("round"), k.get("tick")
        attacker, victim = k.get("attacker"), k.get("victim")
        if rnd is None or tick is None or not victim:
            continue
        fr = nearest_frame(by_round.get(rnd, []), tick)
        if fr is None:
            continue

        planted = bomb_planted_at(match, rnd, tick)
        at, act, et, ect = state_at(match, fr, planted)

        # the victim is still alive in `before` if the sampled frame predates
        # the kill; normalise so `before` always includes them
        vs = k.get("victim_side")
        vent = next((p for p in fr.get("players") or [] if p.get("n") == victim), None)
        vval = _equip_of(match, vent) if vent else 0
        if vent is None or (vent.get("hp") or 0) <= 0:
            if vs == "t":
                at, et = at + 1, et + vval
            elif vs == "ct":
                act, ect = act + 1, ect + vval

        before = win_probability(at, act, planted, et, ect, base)
        if vs == "t":
            after = win_probability(at - 1, act, planted, max(0, et - vval), ect, base)
        elif vs == "ct":
            after = win_probability(at, act - 1, planted, et, max(0, ect - vval), base)
        else:
            continue

        # Positive swing always means "good for the side that acted".
        delta_t = after - before
        aside = k.get("attacker_side")
        if aside == "ct":
            gain = -delta_t
        elif aside == "t":
            gain = delta_t
        else:
            gain = abs(delta_t)        # world/suicide: no actor to credit

        out.append({
            "round": rnd, "tick": tick,
            "attacker": attacker, "victim": victim,
            "attacker_side": aside, "victim_side": vs,
            "p_before_t": round(before, 4),
            "p_after_t": round(after, 4),
            # `+ 0.0` normalises negative zero: a kill in an already-decided
            # round moves the probability by exactly nothing, and -0.0 formats
            # as "-0.0%" which reads like a bug in every display downstream.
            "swing": round(gain, 4) + 0.0,          # for the attacker's side
            "cost": round(-delta_t if vs == "t" else delta_t, 4) + 0.0,  # victim's
            "alive_t": at, "alive_ct": act, "bomb_planted": planted,
        })
    return out


def probability_timeline(match, min_delta=0.004):
    """
    P(T win) at every sampled frame, for a live readout in the viewer.

    Emitted as [tick, p] pairs rather than one per frame: the probability only
    moves when somebody dies or the bomb goes down, so most consecutive frames
    are identical and storing all 4,000 of them is mostly zeros. Points are
    kept when the value moves by `min_delta`, and always at a round's first and
    last frame so every round has a defined start and end.

    Computed here rather than ported to JS on purpose. Two implementations of
    the same model drift, and the one in the browser would be the one nobody
    tests.
    """
    base = side_baseline(match)
    out = []
    for rnd, frames in sorted(frames_by_round(match, live_only=True).items()):
        last = None
        for i, f in enumerate(frames):
            planted = bomb_planted_at(match, rnd, f["tick"])
            at, act, et, ect = state_at(match, f, planted)
            if at == 0 and act == 0:
                continue
            p = win_probability(at, act, planted, et, ect, base)
            edge = i == 0 or i == len(frames) - 1
            if last is None or edge or abs(p - last) >= min_delta:
                # round number rides along so the viewer can tell "no point yet
                # this round" from "the last point of the previous round" - a
                # step lookup alone would show round 4's final probability all
                # through round 5's freeze time.
                out.append([f["tick"], round(p, 4), rnd])
                last = p
    return out


def per_player(match, swings=None):
    """Total and per-round swing for each player, as a percentage."""
    swings = compute_swing(match) if swings is None else swings
    rounds = (match.get("stats") or {}).get("rounds_played") or 1
    agg = defaultdict(lambda: {"swing_gained": 0.0, "swing_lost": 0.0,
                               "kills": 0, "deaths": 0})
    for s in swings:
        if s["attacker"] and s["attacker"] != s["victim"]:
            a = agg[s["attacker"]]
            a["swing_gained"] += s["swing"]
            a["kills"] += 1
        v = agg[s["victim"]]
        v["swing_lost"] += s["cost"]
        v["deaths"] += 1

    out = {}
    for name, a in agg.items():
        # `swing_lost` is stored as a positive magnitude because "this death
        # cost 34%" is the readable form, so net SUBTRACTS it. Adding the two
        # gives every player a large positive number and is the obvious way to
        # get this wrong.
        net = a["swing_gained"] - a["swing_lost"]
        out[name] = {
            "swing_gained_pct": round(100 * a["swing_gained"], 1),
            "swing_lost_pct": round(100 * a["swing_lost"], 1),
            "swing_net_pct": round(100 * net, 1),
            # The per-round figure is the comparable one: HLTV quote Round
            # Swing per round, where elite is around +3 to +4% and most players
            # sit between -1.5% and +1.5%. Ours is a different model, so the
            # scale will not match theirs - but if it comes out an order of
            # magnitude away, something is wrong rather than merely different.
            "swing_per_round_pct": round(100 * net / rounds, 2),
        }
    return out


# ---------------------------------------------------------------------------
# Calibration - does the model actually predict this match?
# ---------------------------------------------------------------------------

def calibrate(match, bins=5):
    """
    Bin every sampled round state by predicted P(T win) and report how often
    the T side actually went on to win. A well-calibrated model has the two
    columns tracking each other.

    Reports the sample size loudly, because one match is ~22 outcomes and
    cannot validate a model - the point is to print the number rather than
    assume it.
    """
    base = side_baseline(match)
    by_round = frames_by_round(match, live_only=True)
    winners = {r["round_num"]: str(r.get("winner", "")).lower()
               for r in match.get("rounds") or []}

    buckets = defaultdict(lambda: {"n": 0, "t_wins": 0, "p_sum": 0.0})
    brier_n = brier_sum = 0.0
    for rnd, frames in by_round.items():
        w = winners.get(rnd)
        if w not in ("t", "ct"):
            continue
        actual = 1.0 if w == "t" else 0.0
        for f in frames:
            planted = bomb_planted_at(match, rnd, f["tick"])
            at, act, et, ect = state_at(match, f, planted)
            if at == 0 and act == 0:
                continue
            p = win_probability(at, act, planted, et, ect, base)
            b = min(bins - 1, int(p * bins))
            buckets[b]["n"] += 1
            buckets[b]["t_wins"] += actual
            buckets[b]["p_sum"] += p
            brier_sum += (p - actual) ** 2
            brier_n += 1

    rows = []
    for b in sorted(buckets):
        d = buckets[b]
        rows.append({
            "band": f"{b/bins:.0%}-{(b+1)/bins:.0%}",
            "n": d["n"],
            "predicted": round(d["p_sum"] / d["n"], 3),
            "actual": round(d["t_wins"] / d["n"], 3),
        })
    return {
        "rounds": len(by_round),
        "states": int(brier_n),
        "brier": round(brier_sum / brier_n, 4) if brier_n else None,
        "baseline_brier": round(base * (1 - base), 4),
        "t_baseline": round(base, 3),
        "bands": rows,
    }


def main():
    ap = argparse.ArgumentParser(description="Round win probability and swing")
    ap.add_argument("match", help="parsed match JSON (data/<name>.json)")
    ap.add_argument("--calibrate", action="store_true",
                    help="report how well the model predicted this match")
    ap.add_argument("--top", type=int, default=10, help="players to list")
    args = ap.parse_args()

    path = Path(args.match).expanduser().resolve()
    if not path.exists():
        sys.exit(f"[!] Not found: {path}")
    match = json.loads(path.read_text())

    if args.calibrate:
        c = calibrate(match)
        print(f"T-side baseline in this match: {c['t_baseline']:.1%}")
        print(f"{c['states']:,} sampled states across {c['rounds']} rounds\n")
        print(f"{'predicted':>12} {'actual':>8} {'states':>8}")
        for r in c["bands"]:
            print(f"{r['predicted']:>12.3f} {r['actual']:>8.3f} {r['n']:>8,}")
        print(f"\nBrier {c['brier']}  (always-guess-the-base-rate = {c['baseline_brier']})")
        print("Lower is better. One match is far too small to validate a model; "
              "run this across many demos before trusting it.")
        return

    rows = per_player(match)
    print(f"{'player':<16}{'gained':>9}{'lost':>9}{'net':>9}{'per round':>11}")
    for name, s in sorted(rows.items(),
                          key=lambda kv: -kv[1]["swing_net_pct"])[:args.top]:
        print(f"{name:<16}{s['swing_gained_pct']:>8.1f}%{s['swing_lost_pct']:>8.1f}%"
              f"{s['swing_net_pct']:>8.1f}%{s['swing_per_round_pct']:>10.2f}%")


if __name__ == "__main__":
    main()
