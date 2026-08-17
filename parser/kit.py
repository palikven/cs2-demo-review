#!/usr/bin/env python3
"""
CS2 Coach - what was still in your hands when you died.

Every kill record carries the victim's inventory, so the grenades a player never
got to throw are already in the data. The interesting question is not how many
there were.

THE COMPARISON CHANGES THE VERDICT HERE, WHICH IS WHY IT SHIPS WITH ONE
-----------------------------------------------------------------------
On the bundled match the POV died holding utility in 6 of 16 deaths - 38%, which
sounds like a habit worth fixing until you look at the other nine players. The
match runs from 36% to 71% and the POV is SECOND BEST. Reported alone, that
number coaches a strength as a weakness.

This is the same lesson as flashes.py and it is the reason both modules carry
the whole table rather than one row.

THE SHARP SIGNAL IS NOT "HELD UTILITY", IT IS "HELD UTILITY WITH TIME TO USE IT"
--------------------------------------------------------------------------------
Dying with a full kit in a 1v3 retake is not a mistake; you never got a turn.
Dying with a full kit on an angle you chose, that you had already lost once, is
a different thing entirely.

So a death only counts here when the duel gave them a turn:

  * a RE-PEEK - they went back to an angle a teammate or they had just lost, so
    they knew what was there and picked the moment, or
  * a STATIC SIGHTLINE - neither player crossed meaningful ground, meaning both
    were already in the angle and nobody was surprised.

On the bundled match that is 3 of 16 deaths, and all three are re-peeks: Middle
holding a smoke, Bridge holding a smoke and two flashes, Main holding an HE, a
molotov and a flash. The claim is not "you waste utility" - it is "when you
re-peek, you re-peek dry", which is specific, checkable and fixable.

WHAT IS NOT CLAIMED
-------------------
Nothing here knows whether a grenade would have HELPED. A smoke is not always
the answer, and the demo cannot tell you whether the angle was smokeable. It
says what was in their hands and whether the duel gave them a turn; the reading
is left to the coach.

Decoys are excluded. Dying with a decoy is not a mistake anybody needs to hear
about.
"""

import collections

# What counts as utility somebody could have used to change the duel. Decoys
# are deliberately absent - see the module docstring.
UTILITY = {
    "Flashbang", "Smoke Grenade", "High Explosive Grenade",
    "Molotov", "Incendiary Grenade",
}

# Ground crossed, in units, below which neither player really moved into the
# duel. Matches duels.py's own notion of a static sightline.
STATIC_UNITS = 50


def held(kill):
    """The utility the victim still had. Order preserved for readability."""
    return [i for i in (kill.get("victim_inv") or []) if i in UTILITY]


def had_a_turn(death):
    """
    Whether the duel gave them a chance to use it.

    A re-peek is a chosen fight on a known angle. A static sightline means
    neither player crossed ground, so nobody was surprised. Everything else -
    getting run into, being caught mid-rotate, the last player alive in a
    retake - is a death where "you still had a flash" is noise.
    """
    if death.get("repeek"):
        return "re-peek"
    if (abs(death.get("victim_closed") or 0) < STATIC_UNITS
            and abs(death.get("attacker_closed") or 0) < STATIC_UNITS):
        return "static sightline"
    return None


def match_table(match):
    """
    Every player's share of deaths holding utility.

    Ships with the profile rather than beside it, because on this match the
    table is what stops a strength being written up as a weakness.
    """
    per = collections.defaultdict(lambda: {"deaths": 0, "with_util": 0,
                                           "nades": 0})
    for k in match.get("kills") or []:
        victim = k.get("victim")
        if not victim:
            continue
        row = per[victim]
        row["deaths"] += 1
        got = held(k)
        if got:
            row["with_util"] += 1
            row["nades"] += len(got)
    out = {}
    for name, row in per.items():
        if not row["deaths"]:
            continue
        out[name] = dict(row,
                         share=round(row["with_util"] / row["deaths"], 3))
    return out


def profile(match, player, deaths=None):
    """
    One player's record, with the match around it.

    `deaths` is the analysed death list - it carries repeek and the closure
    figures, which is what separates "had a turn" from "never got one". Without
    it only the raw counts are available, and those are the misleading half.
    """
    kills_by_tick = {}
    for k in match.get("kills") or []:
        if k.get("victim") == player:
            kills_by_tick[k["tick"]] = k

    table = match_table(match)
    mine = table.get(player, {"deaths": 0, "with_util": 0, "nades": 0,
                              "share": None})

    ranked = sorted(table.items(), key=lambda kv: kv[1]["share"])
    rank = next((i + 1 for i, (n, _) in enumerate(ranked) if n == player), None)

    missed = []
    for d in (deaths or []):
        k = kills_by_tick.get(d.get("tick"))
        if not k:
            continue
        got = held(k)
        if not got:
            continue
        turn = had_a_turn(d)
        if turn:
            missed.append({"round": d.get("round"), "tick": d.get("tick"),
                           "place": d.get("victim_place"), "why": turn,
                           "held": got})

    return {
        "player": player,
        "deaths": mine["deaths"],
        "deaths_holding_utility": mine["with_util"],
        "share": mine["share"],
        "rank": rank,
        "of": len(ranked),
        "unused_nades": mine["nades"],
        "with_a_turn": missed,
        "table": {n: t["share"] for n, t in ranked},
        "note": ("Share alone means nothing without the table - on a match "
                 "where everyone is between 36% and 71%, being at 38% is good. "
                 "Only `with_a_turn` is worth coaching: deaths where the duel "
                 "actually gave them a chance to throw something. Nothing here "
                 "knows whether a grenade would have helped."),
    }


def main():
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: kit.py data/match.json [player]")
        return 1
    match = json.load(open(sys.argv[1], encoding="utf-8"))
    player = sys.argv[2] if len(sys.argv) > 2 else match.get("pov")

    deaths = []
    try:
        import analyze
        deaths = analyze.analyze(match, player).get("deaths") or []
    except Exception as exc:                                # noqa: BLE001
        print(f"[!] no death review ({exc}); showing counts only\n")

    p = profile(match, player, deaths)
    print("DIED STILL HOLDING UTILITY\n")
    print(f"  {'player':<14}{'deaths':>7}{'w/ util':>9}{'share':>7}")
    for name, share in p["table"].items():
        t = match_table(match)[name]
        mark = "  <- you" if name == player else ""
        print(f"  {name:<14}{t['deaths']:>7}{t['with_util']:>9}"
              f"{share * 100:>6.0f}%{mark}")

    if p["share"] is not None:
        print(f"\n{player}: {p['deaths_holding_utility']} of {p['deaths']} "
              f"deaths ({p['share'] * 100:.0f}%), rank {p['rank']} of {p['of']} "
              f"- lower is better.")
    if p["with_a_turn"]:
        print(f"\n  {len(p['with_a_turn'])} of those came in duels that gave "
              f"them a turn:")
        for h in p["with_a_turn"]:
            print(f"    R{h['round']:>2} {str(h['place']):<12} {h['why']:<17} "
                  f"held {', '.join(h['held'])}")
    else:
        print("\n  None of them came in a duel that gave them a turn.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
