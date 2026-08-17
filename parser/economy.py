#!/usr/bin/env python3
"""
CS2 Coach - round economy, and what a death costs in guns.

The other half of "when should I not have peeked" is "and what should I do in
the rounds after". That question is mostly about money: the same push is correct
on a full buy against a full buy and round-losing on a full buy against an eco,
because the second one hands them a rifle they could not afford.

TWO THINGS ARE COMPUTED, AND THEY ANSWER DIFFERENT QUESTIONS
------------------------------------------------------------
  buy state    per round, per side: total equipment value at freeze-end, as a
               fraction of what a full buy for that many players costs. This is
               the CONTEXT a death happened in. Dying at 5v5 while they were on
               an eco is a different mistake from the same death in a gun round,
               and coaching them identically is how you get advice that is wrong
               half the time.

  duel value   per kill: what each side had on the line. Losing a $2700 rifle to
               someone risking a $700 Deagle is a compounding loss that no
               scoreboard shows - you are down a round AND they are up a rifle.

WHAT THIS DOES NOT SAY
----------------------
It never says the enemy picked your gun up. They often do, and it is not in this
data - they may not have had time, or the gun fell somewhere they never went.
The asymmetry is a fact; the pickup is an inference, so the wording throughout
is "you lost X while they risked Y". A model handed "you armed them" will state
it as something that happened.

It also does not track money, only equipment. The demo gives what people are
holding, not what is in their bank, so "they were saving" is not something this
can tell you - only that they were not carrying much.

VALUATION
---------
parse_demo.py exports `loadout_values`: the price of the most expensive weapon
in each distinct loadout, parallel to `loadouts`. Armour is on the frame entry.
That deliberately excludes grenades, so these totals run below HLTV's published
equipment values and must not be compared against them. They are compared
against a full buy on the same scale, which is the only comparison that means
anything here.
"""

import collections

from prices import WEAPON_PRICES

# Fractions of a full buy for the whole side. A pistol round lands around 0.20
# on this scale, a full gun round at 0.85-1.0.
ECO_MAX = 0.30
FORCE_MAX = 0.55
SEMI_MAX = 0.85

# A rifle lost to a pistol. Both floors: the gap has to be real money AND the
# thing lost has to be worth something, or every knife round is a disaster.
ARMED_THEM_MIN_VALUE = 2000
ARMED_THEM_MIN_GAP = 1500

DEFAULT_FULL_BUY = 3700          # rifle + vest + helmet, per player


def _full_buy_per_player(match):
    return ((match.get("armor_prices") or {}).get("full_buy")
            or DEFAULT_FULL_BUY)


def _vest_price(match):
    return (match.get("armor_prices") or {}).get("vest", 650)


def weapon_value(match, inventory):
    """
    The price of the most expensive weapon someone was carrying.

    Kills record inventories as item NAMES rather than a loadout index, so this
    prices them against the same table parse_demo used - one source of prices,
    not two that can disagree about what an AK costs.
    """
    return max((WEAPON_PRICES.get(i, 0) for i in (inventory or [])), default=0)


def _freeze_end_frame(match, rnd):
    """
    The first frame after buy time closed.

    Not the round start: during freeze time players are still buying, and read
    a tick early the whole side looks like an eco.
    """
    info = next((r for r in match.get("rounds") or []
                 if r.get("round_num") == rnd), None)
    if not info:
        return None
    fe = info.get("freeze_end")
    if fe is None:
        return None
    for f in match.get("frames") or []:
        if f.get("round") == rnd and f["tick"] >= fe:
            return f
    return None


def pistol_rounds(match):
    """
    Round 1 and the first round after the sides swap.

    Worth naming separately from an eco: everyone is on a pistol by rule rather
    than by choice, so none of the buy-discipline coaching applies.

    The swap is detected rather than assumed at 13 - the format is not in the
    demo, and MR12, MR15 and overtime all put it in different places.
    """
    frames = match.get("frames") or []
    if not frames:
        return set()
    start_side = {p["n"]: p.get("t") for p in frames[0].get("players") or []}
    out = {1}
    for r in sorted(x.get("round_num") for x in match.get("rounds") or []
                    if x.get("round_num")):
        f = _freeze_end_frame(match, r)
        if not f:
            continue
        players = f.get("players") or []
        flipped = sum(1 for p in players
                      if start_side.get(p["n"]) and p.get("t") != start_side[p["n"]])
        if players and flipped >= len(players) / 2:
            out.add(r)
            break
    return out


def classify(fraction, pistol=False):
    """Buy state from the fraction of a full buy the side is carrying."""
    if pistol:
        return "pistol"
    if fraction <= ECO_MAX:
        return "eco"
    if fraction <= FORCE_MAX:
        return "force"
    if fraction <= SEMI_MAX:
        return "semi"
    return "full"


def buy_state(match):
    """
    {round: {"t": {...}, "ct": {...}}} at freeze-end.

    The denominator is the number of players actually on that side in that
    frame, not a hard-coded five: a 4v5 round is not an eco just because
    somebody disconnected.
    """
    values = match.get("loadout_values") or []
    vest = _vest_price(match)
    per_player = _full_buy_per_player(match)
    pistols = pistol_rounds(match)

    out = {}
    for info in match.get("rounds") or []:
        rnd = info.get("round_num")
        if rnd is None:
            continue
        f = _freeze_end_frame(match, rnd)
        if not f:
            continue
        total = collections.defaultdict(int)
        count = collections.Counter()
        for p in f.get("players") or []:
            side = (p.get("t") or "").lower()
            if side not in ("t", "ct"):
                continue
            eq = p.get("eq")
            gun = values[eq] if isinstance(eq, int) and 0 <= eq < len(values) else 0
            total[side] += gun + (vest if p.get("ar", 0) > 0 else 0)
            count[side] += 1
        sides = {}
        for side in ("t", "ct"):
            if not count[side]:
                continue
            frac = total[side] / (count[side] * per_player)
            sides[side] = {
                "value": total[side],
                "players": count[side],
                "fraction": round(frac, 3),
                "state": classify(frac, pistol=rnd in pistols),
            }
        if sides:
            out[rnd] = sides
    return out


def duel_value(match, kill):
    """
    What each side had on the line in one duel.

    `lost` is the victim's gun, `risked` the attacker's. `armed_them` marks the
    case worth coaching: something expensive lost to something cheap. It does
    NOT mean the gun was picked up - that is not in the demo.
    """
    if not kill.get("attacker") or kill["attacker"] == kill.get("victim"):
        return None
    lost = weapon_value(match, kill.get("victim_inv"))
    risked = weapon_value(match, kill.get("attacker_inv"))
    return {
        "lost": lost,
        "risked": risked,
        "delta": lost - risked,
        "armed_them": (lost >= ARMED_THEM_MIN_VALUE
                       and lost - risked >= ARMED_THEM_MIN_GAP),
    }


def annotate(match, deaths, kills, player):
    """Attach buy state and duel value to an existing review, in place."""
    buys = buy_state(match)
    by_tick = collections.defaultdict(list)
    for k in match.get("kills") or []:
        by_tick[k["tick"]].append(k)

    def apply(rec, as_victim):
        rnd, tick = rec.get("round"), rec.get("tick")
        sides = buys.get(rnd)
        if sides:
            mine = (rec.get("victim_side") if as_victim
                    else rec.get("attacker_side")) or rec.get("side")
            mine = (mine or "").lower()
            theirs = "ct" if mine == "t" else "t" if mine == "ct" else None
            if mine in sides:
                rec["buy"] = sides[mine]["state"]
                rec["buy_value"] = sides[mine]["value"]
            if theirs in sides:
                rec["enemy_buy"] = sides[theirs]["state"]
                rec["enemy_buy_value"] = sides[theirs]["value"]
        for k in by_tick.get(tick, []):
            hit = (k.get("victim") == player if as_victim
                   else k.get("attacker") == player)
            if not hit:
                continue
            dv = duel_value(match, k)
            if dv:
                rec.update({"gun_lost": dv["lost"], "gun_risked": dv["risked"]}
                           if as_victim else
                           {"gun_taken": dv["lost"], "gun_held": dv["risked"]})
                if as_victim and dv["armed_them"]:
                    rec["armed_them"] = True
            break

    for d in deaths:
        apply(d, True)
    for c in kills:
        apply(c, False)
    return deaths, kills


def profile(match, player):
    """
    The player's economy across the match.

    Counts only, no significance test: these are sums of prices rather than a
    distribution being compared to another one. "You lost $38,200 of guns and
    they risked $31,450 taking them" is a total, and a total needs no p-value -
    but it also is not a tendency, so nothing here should be phrased as one.
    """
    lost = risked = taken = held = 0
    armed = []
    deaths = kills = 0
    buys = buy_state(match)
    against_poorer = 0

    for k in match.get("kills") or []:
        dv = duel_value(match, k)
        if not dv:
            continue
        if k.get("victim") == player:
            deaths += 1
            lost += dv["lost"]
            risked += dv["risked"]
            if dv["armed_them"]:
                armed.append({"round": k.get("round"), "tick": k["tick"],
                              "lost": dv["lost"], "risked": dv["risked"],
                              "weapon": k.get("weapon")})
            sides = buys.get(k.get("round")) or {}
            mine = (k.get("victim_side") or "").lower()
            theirs = "ct" if mine == "t" else "t" if mine == "ct" else None
            if (mine in sides and theirs in sides
                    and sides[mine]["fraction"] - sides[theirs]["fraction"] > 0.25):
                against_poorer += 1
        elif k.get("attacker") == player:
            kills += 1
            taken += dv["lost"]
            held += dv["risked"]

    return {
        "player": player,
        "deaths_priced": deaths,
        "kills_priced": kills,
        "value_lost": lost,
        "value_risked_against_you": risked,
        "value_taken": taken,
        "value_you_held": held,
        "net": taken - lost,
        "armed_them": armed,
        "deaths_while_richer": against_poorer,
        "note": ("These are equipment prices, not money. The demo shows what "
                 "people carried, not what they banked, and it does not show "
                 "whether a dropped gun was picked up - say what was lost and "
                 "risked, not that the enemy was armed."),
    }


def main():
    import json
    import sys
    if len(sys.argv) < 2:
        print("usage: economy.py data/match.json [player]")
        return 1
    match = json.load(open(sys.argv[1], encoding="utf-8"))
    player = sys.argv[2] if len(sys.argv) > 2 else match.get("pov")

    print("ROUND BUYS (equipment only - no grenades, so not HLTV's scale)\n")
    for rnd, sides in sorted(buy_state(match).items()):
        bits = "  ".join(
            f"{s.upper():>2} ${v['value']:>6} {v['state']:<6}"
            for s, v in sorted(sides.items()))
        print(f"  R{rnd:>2}  {bits}")

    prof = profile(match, player)
    print(f"\n{player}: lost ${prof['value_lost']} of guns across "
          f"{prof['deaths_priced']} deaths, taking ${prof['value_taken']} "
          f"across {prof['kills_priced']} kills (net ${prof['net']:+}).")
    if prof["armed_them"]:
        print("\n  Expensive guns lost to cheap ones:")
        for a in prof["armed_them"]:
            print(f"    R{a['round']:>2}: ${a['lost']} lost to a ${a['risked']} "
                  f"{a['weapon']}")
    if prof["deaths_while_richer"]:
        print(f"\n  {prof['deaths_while_richer']} deaths came in rounds where "
              f"your side was the better-equipped one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
