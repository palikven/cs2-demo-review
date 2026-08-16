#!/usr/bin/env python3
"""
CS2 Team Coach - positional analysis (Stage 2.5)

Turns the parsed match into DETERMINISTIC, checkable statements about *where*
things happened: which callout you died in, who killed you from where, whether
anyone was close enough to trade you, and whether you had just watched a
teammate lose the same duel.

This module is the whole reason the coaching layer can say anything a stats
site can't. It is also the guard rail: everything here is arithmetic over the
demo, so every claim the AI layer makes downstream traces back to a number you
can go and count yourself. The model is handed callout NAMES and COUNTS - never
coordinates - which is the project's one hard design rule.

Pure functions, no I/O, no network. Import it, or run it for a text dump:

    python analyze.py data/your_match.json [--player NAME]
"""

import argparse
import json
import math
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path

# parse_demo isn't a package, and analyze.py sits next to it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gates import reliable  # noqa: E402,F401  (re-exported for callers)
from parse_demo import TRADE_WINDOW_TICKS, is_player_kill, is_trade  # noqa: E402

# A teammate further than this from you when you died was not going to trade
# you, whatever the killfeed says. ~1100 units is a long hallway - far enough
# that "nobody was near me" is a fair description, close enough that normal
# spread inside a site doesn't get flagged.
ISOLATED_UNITS = 1100.0

# Watching a teammate die somewhere and then walking into the same spot is the
# single most correctable habit in the game. 8s is long enough to cover reload
# + reposition, short enough that the enemy is probably still holding.
REPEEK_SECONDS = 8.0

# Round phases, in seconds after freeze time ends. CS2 rounds are 1:55.
PHASE_EARLY_MAX = 20.0
PHASE_MID_MAX = 70.0

AWP_WEAPONS = {"awp", "ssg08", "scar20", "g3sg1"}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _sides_differ(a, b):
    """True only when we KNOW two sides differ. Missing data means unknown."""
    return a is not None and b is not None and a != b


def _same_side(a, b):
    return a is not None and b is not None and a == b


def dist2d(ax, ay, bx, by):
    """
    Ground distance. Deliberately 2D even though the parser now emits z.

    Spacing is a tactical idea, not a geometric one: a teammate thirty units
    directly above you on Nuke is not supporting your duel, and folding that
    height into the distance would make the pair look closer than they play.
    """
    return math.hypot(ax - bx, ay - by)


class MatchIndex:
    """Precomputed lookups. Built once, reused for every death in the match."""

    def __init__(self, match):
        self.match = match
        self.tickrate = match.get("tickrate") or 64
        self.place_names = match.get("place_names") or []
        self.frames = sorted(
            (f for f in (match.get("frames") or []) if f.get("tick") is not None),
            key=lambda f: f["tick"],
        )
        self._frame_ticks = [f["tick"] for f in self.frames]

        self.rounds = {}
        for r in match.get("rounds") or []:
            if isinstance(r, dict) and r.get("round_num") is not None:
                self.rounds[r["round_num"]] = r

        self.kills = sorted(
            (k for k in (match.get("kills") or []) if k.get("tick") is not None),
            key=lambda k: k["tick"],
        )
        self.by_round = defaultdict(list)
        for k in self.kills:
            self.by_round[k.get("round")].append(k)

        # side per player, taken from whichever kill records it. Rounds swap
        # sides at half time, so this is only ever used within a round.
        self.player_names = [p["name"] for p in (match.get("players") or []) if p.get("name")]

    def frame_at(self, tick):
        """
        The last sampled frame at or before `tick` - the world as it stood just
        before the event. Positions are sampled every ~0.5s, so this is up to
        half a second stale; that is close enough for alive counts and spacing,
        and the kill record carries exact coordinates for the two players who
        actually matter.
        """
        if not self.frames:
            return None
        i = bisect_right(self._frame_ticks, tick) - 1
        if i < 0:
            return self.frames[0]
        return self.frames[i]

    def place_of(self, entry):
        idx = entry.get("pl")
        if idx is None or idx >= len(self.place_names):
            return None
        return self.place_names[idx]

    def seconds_into_round(self, rnd, tick):
        r = self.rounds.get(rnd)
        if not r or tick is None:
            return None
        start = r.get("freeze_end") or r.get("start")
        if start is None:
            return None
        return round(max(0.0, (tick - start) / self.tickrate), 1)

    def is_post_plant(self, rnd, tick):
        r = self.rounds.get(rnd)
        if not r:
            return False
        plant = r.get("bomb_plant")
        return plant is not None and tick is not None and tick >= plant


def alive_at(idx, tick):
    """(alive_by_side, living_entries) just before `tick`."""
    fr = idx.frame_at(tick)
    if not fr:
        return {}, []
    living = []
    for p in fr.get("players") or []:
        if p.get("a") is False:
            continue
        if p.get("hp") is not None and p["hp"] <= 0:
            continue
        living.append(p)
    counts = Counter((p.get("t") or "?").lower() for p in living)
    return dict(counts), living


# --------------------------------------------------------------------------
# per-death classification
# --------------------------------------------------------------------------
def classify_death(kill, idx):
    """
    Everything we can prove about one death. Returns None for deaths nobody
    can be coached about (world damage, suicides).
    """
    if not is_player_kill(kill):
        return None

    tick = kill["tick"]
    rnd = kill.get("round")
    victim, attacker = kill["victim"], kill["attacker"]
    v_side = kill.get("victim_side")

    counts, living = alive_at(idx, tick)
    # Sides are "t"/"ct" in the frames and on kill records alike.
    mine = counts.get((v_side or "").lower(), 0)
    theirs = sum(n for s, n in counts.items() if s != (v_side or "").lower() and s != "?")
    # The victim is still standing in the frame we sampled, so their own body
    # is included in `mine`. Compare the situation they walked into.
    man_state = "even" if mine == theirs else ("up" if mine > theirs else "down")

    # spacing: nearest living teammate to where the victim actually fell
    nearest = None
    nearest_name = None
    vx, vy = kill.get("vx"), kill.get("vy")
    if vx is not None and vy is not None:
        for p in living:
            if p.get("n") == victim:
                continue
            if not _same_side(p.get("t"), v_side):
                continue
            d = dist2d(vx, vy, p.get("x", 0.0), p.get("y", 0.0))
            if nearest is None or d < nearest:
                nearest, nearest_name = d, p.get("n")

    # traded?
    round_kills = idx.by_round.get(rnd, [])
    try:
        pos = round_kills.index(kill)
    except ValueError:
        pos = -1
    traded = is_trade(kill, round_kills[pos + 1:]) if pos >= 0 else False

    # opening duel = first player kill of the round
    opener = next((k for k in round_kills if is_player_kill(k)), None)
    opening = opener is not None and opener is kill

    # re-peek: did a teammate lose a duel in this same callout, just now?
    repeek = False
    repeek_after = None
    vplace = kill.get("victim_place")
    if vplace:
        window = REPEEK_SECONDS * idx.tickrate
        for earlier in round_kills:
            if earlier is kill or earlier["tick"] >= tick:
                continue
            if tick - earlier["tick"] > window:
                continue
            if not is_player_kill(earlier):
                continue
            if earlier.get("victim_place") != vplace:
                continue
            if not _same_side(earlier.get("victim_side"), v_side):
                continue
            repeek = True
            repeek_after = round((tick - earlier["tick"]) / idx.tickrate, 1)
            break

    weapon = (kill.get("weapon") or "").lower()
    out = {
        "round": rnd,
        "tick": tick,
        "t": idx.seconds_into_round(rnd, tick),
        "victim": victim,
        "attacker": attacker,
        "victim_side": v_side,
        "attacker_side": kill.get("attacker_side"),
        "victim_place": vplace,
        "attacker_place": kill.get("attacker_place"),
        "weapon": weapon or None,
        "awp": weapon in AWP_WEAPONS,
        "hs": bool(kill.get("hs")),
        "alive_own": mine,
        "alive_enemy": theirs,
        "man_state": man_state,
        "traded": traded,
        "opening": opening,
        "repeek": repeek,
        "post_plant": idx.is_post_plant(rnd, tick),
    }
    if repeek_after is not None:
        out["repeek_after"] = repeek_after
    if nearest is not None:
        out["nearest_mate"] = round(nearest, 0)
        out["nearest_mate_name"] = nearest_name
        out["isolated"] = nearest > ISOLATED_UNITS
    if kill.get("dist") is not None:
        out["range"] = kill["dist"]
    for flag in ("blind", "smoke", "air", "noscope", "wallbang"):
        if kill.get(flag):
            out[flag] = True
    return out


def phase_of(seconds, post_plant):
    if post_plant:
        return "post_plant"
    if seconds is None:
        return "unknown"
    if seconds <= PHASE_EARLY_MAX:
        return "early"
    if seconds <= PHASE_MID_MAX:
        return "mid"
    return "late"


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------
def death_hotspots(deaths, min_deaths=2):
    """
    Where you keep dying, and where it keeps coming from.

    Grouped by (where you died, where they shot you from) rather than by your
    location alone - "I die in Palace" is a map fact, "I die in Palace to
    someone in Connector, untraded, four times" is a fixable habit.
    """
    buckets = defaultdict(lambda: {"deaths": 0, "untraded": 0, "repeeks": 0,
                                   "opening": 0, "rounds": []})
    for d in deaths:
        if not d.get("victim_place"):
            continue
        key = (d["victim_place"], d.get("attacker_place"))
        b = buckets[key]
        b["deaths"] += 1
        b["untraded"] += 0 if d["traded"] else 1
        b["repeeks"] += 1 if d["repeek"] else 0
        b["opening"] += 1 if d["opening"] else 0
        b["rounds"].append(d["round"])
    out = []
    for (vp, ap), b in buckets.items():
        if b["deaths"] < min_deaths:
            continue
        out.append({"place": vp, "from": ap, **b})
    out.sort(key=lambda x: (-x["deaths"], -x["untraded"]))
    return out


def death_places(deaths, min_deaths=2):
    """
    Where you die, ignoring who killed you.

    death_hotspots() pairs your location with theirs, which is the sharper
    finding when it lands - but it also splits one habit across several rows.
    Dying in Main five times to four different angles is invisible to that
    grouping and is exactly the pattern worth naming, so roll it up separately
    and carry the angles along as context.
    """
    buckets = defaultdict(lambda: {"deaths": 0, "untraded": 0, "repeeks": 0,
                                   "opening": 0, "from": Counter(), "rounds": []})
    for d in deaths:
        if not d.get("victim_place"):
            continue
        b = buckets[d["victim_place"]]
        b["deaths"] += 1
        b["untraded"] += 0 if d["traded"] else 1
        b["repeeks"] += 1 if d["repeek"] else 0
        b["opening"] += 1 if d["opening"] else 0
        b["rounds"].append(d["round"])
        if d.get("attacker_place"):
            b["from"][d["attacker_place"]] += 1
    out = []
    for place, b in buckets.items():
        if b["deaths"] < min_deaths:
            continue
        out.append({
            "place": place,
            "deaths": b["deaths"],
            "untraded": b["untraded"],
            "repeeks": b["repeeks"],
            "opening": b["opening"],
            "rounds": sorted(b["rounds"]),
            "from": [{"place": p, "n": n} for p, n in b["from"].most_common()],
        })
    out.sort(key=lambda x: (-x["deaths"], -x["untraded"]))
    return out


def phase_profile(deaths):
    prof = {}
    for d in deaths:
        ph = phase_of(d.get("t"), d.get("post_plant"))
        p = prof.setdefault(ph, {"deaths": 0, "untraded": 0, "man_down": 0, "isolated": 0})
        p["deaths"] += 1
        p["untraded"] += 0 if d["traded"] else 1
        p["man_down"] += 1 if d["man_state"] == "down" else 0
        p["isolated"] += 1 if d.get("isolated") else 0
    return prof


def isolation_profile(deaths, kills):
    """
    How far from support you are when you die, versus when you win.

    The gap is the number behind "you play too far from your team": if you're
    consistently more isolated in the duels you lose than the ones you win,
    that's positioning, not aim.
    """
    dd = [d["nearest_mate"] for d in deaths if d.get("nearest_mate") is not None]
    kk = [k["nearest_mate"] for k in kills if k.get("nearest_mate") is not None]
    prof = {
        "deaths_measured": len(dd),
        "kills_measured": len(kk),
        "isolated_deaths": sum(1 for d in deaths if d.get("isolated")),
        "isolated_threshold": ISOLATED_UNITS,
    }
    if dd:
        prof["median_dist_at_death"] = round(sorted(dd)[len(dd) // 2], 0)
    if kk:
        prof["median_dist_at_kill"] = round(sorted(kk)[len(kk) // 2], 0)
    return prof


def snapshot(match, tick, player=None):
    """
    The state of the map at one instant, in callouts.

    This is what the live "should I peek here?" endpoint hands the model. Same
    rule as everywhere else: named places and counts, no coordinates. Distances
    are included because they're a measurement of spacing, not a position - the
    model can't reconstruct the map from "your nearest teammate is 400 units
    away", but it can tell you nobody is close enough to trade you.
    """
    idx = MatchIndex(match)
    name = player or match.get("pov")
    fr = idx.frame_at(tick)
    if not fr:
        return None

    rnd = fr.get("round")
    living = [p for p in (fr.get("players") or [])
              if p.get("a") is not False and (p.get("hp") is None or p["hp"] > 0)]

    me = next((p for p in (fr.get("players") or []) if p.get("n") == name), None)
    my_side = (me or {}).get("t")
    my_alive = me is not None and me.get("a") is not False and (me.get("hp") or 0) > 0

    # Enemies are COUNTED, never described. The player didn't know where they
    # were, so neither does the coach - advice built on the enemy's actual
    # position is unfollowable in the moment it's about ("you should have known
    # he was in Connector" is not coaching).
    #
    # Note this is enforced by never building the enemy records at all, rather
    # than by building them and stripping fields afterwards. Same result, but
    # there is no populated structure sitting around for a later edit to
    # accidentally include.
    teammates, enemies_alive = [], 0
    for p in living:
        if p.get("n") == name:
            continue
        if not _same_side(p.get("t"), my_side):
            enemies_alive += 1
            continue
        entry = {"name": p.get("n"), "place": idx.place_of(p), "hp": p.get("hp")}
        if me is not None and me.get("x") is not None and p.get("x") is not None:
            entry["dist"] = round(dist2d(me["x"], me["y"], p["x"], p["y"]), 0)
        teammates.append(entry)

    prior = [k for k in idx.by_round.get(rnd, [])
             if k["tick"] <= tick and is_player_kill(k)]
    return {
        "player": name,
        "round": rnd,
        "t": idx.seconds_into_round(rnd, tick),
        "alive": my_alive,
        "my_place": idx.place_of(me) if me else None,
        "my_hp": (me or {}).get("hp"),
        "my_side": my_side,
        "teammates": sorted(teammates, key=lambda x: x.get("dist") or 1e9),
        "enemies_alive": enemies_alive,
        "teammates_alive": len(teammates),
        "post_plant": idx.is_post_plant(rnd, tick),
        "round_so_far": [
            {"t": idx.seconds_into_round(rnd, k["tick"]),
             "attacker": k["attacker"], "victim": k["victim"],
             "victim_place": k.get("victim_place"),
             "attacker_place": k.get("attacker_place"),
             "mine": _same_side(k.get("victim_side"), my_side)}
            for k in prior
        ],
    }


def classify_kill(kill, idx):
    """The winning half of a duel - only the fields the isolation gap needs."""
    if not is_player_kill(kill):
        return None
    counts, living = alive_at(idx, kill["tick"])
    a_side = kill.get("attacker_side")
    ax, ay = kill.get("ax"), kill.get("ay")
    nearest = None
    if ax is not None and ay is not None:
        for p in living:
            if p.get("n") == kill["attacker"] or not _same_side(p.get("t"), a_side):
                continue
            d = dist2d(ax, ay, p.get("x", 0.0), p.get("y", 0.0))
            if nearest is None or d < nearest:
                nearest = d
    out = {
        "round": kill.get("round"),
        "tick": kill["tick"],
        "t": idx.seconds_into_round(kill.get("round"), kill["tick"]),
        "attacker": kill["attacker"],
        "victim": kill["victim"],
        "attacker_place": kill.get("attacker_place"),
        "victim_place": kill.get("victim_place"),
        "weapon": (kill.get("weapon") or "").lower() or None,
    }
    if nearest is not None:
        out["nearest_mate"] = round(nearest, 0)
    return out


def analyze(match, player=None):
    """
    Full positional analysis for one player (defaults to the match POV).

    Everything returned is countable from the demo. Nothing here is a judgement
    - the judgements happen one layer up, in coach.py, working only from these.
    """
    idx = MatchIndex(match)
    name = player or match.get("pov") or (idx.player_names[0] if idx.player_names else None)

    deaths, kills = [], []
    for k in idx.kills:
        if k.get("victim") == name:
            d = classify_death(k, idx)
            if d:
                deaths.append(d)
        if k.get("attacker") == name:
            c = classify_kill(k, idx)
            if c:
                kills.append(c)

    total_deaths = len(deaths)
    hotspots = death_hotspots(deaths)
    places = death_places(deaths)
    # Only surface a pattern the data actually supports. Two deaths in one
    # matchup out of twenty is noise; the gate is the same one narrate.py uses
    # for flash assists, for the same reason.
    for group in (hotspots, places):
        for h in group:
            h["notable"] = reliable(h["deaths"], total_deaths, min_count=2,
                                    min_frac=0.12, min_sources=1, sources=1)

    # Swing: what each death actually cost the team, rather than just that it
    # happened. Attached by tick so a death card can say "this one cost 34% of
    # the round" instead of leaving the reader to judge from the alive counts.
    swing_by_tick, swing_totals, swing_all = {}, {}, {}
    win_prob, swing_events = [], []
    try:
        import swing as swingmod
        for s in swingmod.compute_swing(match):
            if s["victim"] == name:
                swing_by_tick[s["tick"]] = s
            if s["attacker"] == name and s["attacker"] != s["victim"]:
                swing_by_tick.setdefault(s["tick"], s)
        # Everyone's, not just the reviewed player's - the viewer shows swing
        # beside each name in the team panel, and computing it for one player
        # and discarding the other nine was wasted work anyway.
        swing_all = swingmod.per_player(match)
        swing_totals = swing_all.get(name, {})
        # Live readout: P(T win) over time, plus every kill's swing so the
        # viewer can accumulate a player's contribution as a round plays.
        win_prob = swingmod.probability_timeline(match)
        swing_events = [
            {"t": s["tick"], "r": s["round"], "a": s["attacker"],
             "v": s["victim"], "g": s["swing"], "c": s["cost"]}
            for s in swingmod.compute_swing(match)
        ]
    except Exception as exc:                       # noqa: BLE001
        # The review is useful without swing; losing it should not lose the
        # rest of the analysis.
        print(f"[!] swing unavailable ({exc}); continuing without it", file=sys.stderr)

    for d in deaths:
        s = swing_by_tick.get(d["tick"])
        if s:
            d["swing_cost_pct"] = round(100 * s["cost"], 1)
            d["win_prob_before"] = s["p_before_t"] if d.get("side") == "t" else \
                round(1 - s["p_before_t"], 4)
    for k in kills:
        s = swing_by_tick.get(k.get("tick"))
        if s and s.get("attacker") == name:
            k["swing_gain_pct"] = round(100 * s["swing"], 1)

    return {
        "player": name,
        "map": match.get("map"),
        "rounds_played": (match.get("stats") or {}).get("rounds_played"),
        "swing": swing_totals,
        "swing_all": swing_all,
        # [[tick, P(T win)], ...] - only where it moves, so the viewer can
        # show a live probability without re-deriving the model in JS.
        "win_prob": win_prob,
        "swing_events": swing_events,
        "deaths": deaths,
        "kills": kills,
        "death_places": places,
        "death_hotspots": hotspots,
        "phase_profile": phase_profile(deaths),
        "isolation_profile": isolation_profile(deaths, kills),
        "totals": {
            "deaths": total_deaths,
            "untraded": sum(1 for d in deaths if not d["traded"]),
            "opening_deaths": sum(1 for d in deaths if d["opening"]),
            "repeek_deaths": sum(1 for d in deaths if d["repeek"]),
            "deaths_while_down": sum(1 for d in deaths if d["man_state"] == "down"),
            "deaths_to_awp": sum(1 for d in deaths if d["awp"]),
            "kills": len(kills),
        },
    }


# --------------------------------------------------------------------------
def _fmt_place(p):
    return p or "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("match", help="data/<match>.json from parse_demo.py")
    ap.add_argument("--player", default=None, help="defaults to the match POV")
    ap.add_argument("--emit", action="store_true",
                    help="write data/<match>_analysis.json for the viewer")
    args = ap.parse_args()

    path = Path(args.match)
    match = json.loads(path.read_text())
    a = analyze(match, args.player)

    if args.emit:
        # The viewer's death review is built from THIS, not from the AI layer.
        # Everything here is arithmetic, so the panel works with no API key and
        # coach.json only ever adds interpretation on top of it.
        out_path = path.with_name(path.stem + "_analysis.json")
        out_path.write_text(json.dumps(a, separators=(",", ":")))
        size = out_path.stat().st_size / 1000
        print(f"[+] Wrote {out_path.name} ({size:.0f} KB, "
              f"{a['totals']['deaths']} deaths for {a['player']})")
        return

    print(f"{a['player']} on {a['map']} - {a['totals']['deaths']} deaths, "
          f"{a['totals']['kills']} kills over {a['rounds_played']} rounds\n")

    print("DEATHS")
    for d in a["deaths"]:
        bits = [f"R{d['round']:>2}", f"{str(d['t']) + 's':>6}",
                f"{_fmt_place(d['victim_place']):<16}",
                f"<- {_fmt_place(d['attacker_place']):<16}",
                f"{d['weapon'] or '?':<10}",
                f"{d['alive_own']}v{d['alive_enemy']}"]
        if d.get("swing_cost_pct") is not None:
            # A death in an already-decided round genuinely costs nothing;
            # printing "-0.0%" makes that look like a rounding artefact.
            cost = d["swing_cost_pct"]
            bits.append("  decided" if cost < 0.05 else f"{-cost:>6.1f}%")
        tags = [t for t, on in (("opening", d["opening"]), ("untraded", not d["traded"]),
                                ("repeek", d["repeek"]), ("isolated", d.get("isolated")),
                                ("post-plant", d["post_plant"])) if on]
        if d.get("nearest_mate") is not None:
            tags.append(f"mate {int(d['nearest_mate'])}u")
        print("  " + " ".join(bits) + ("   " + ", ".join(tags) if tags else ""))

    print("\nWHERE YOU DIE")
    for h in a["death_places"]:
        angles = ", ".join(f"{f['place']}x{f['n']}" for f in h["from"][:4])
        print(f"  {_fmt_place(h['place']):<16} {h['deaths']}x  untraded {h['untraded']}  "
              f"repeek {h['repeeks']}  rounds {h['rounds']}"
              f"{'  [notable]' if h['notable'] else ''}\n"
              f"      killed from: {angles or '?'}")

    print("\nHOTSPOTS (place <- angle)")
    for h in a["death_hotspots"]:
        print(f"  {_fmt_place(h['place']):<16} <- {_fmt_place(h['from']):<16} "
              f"{h['deaths']}x  untraded {h['untraded']}  repeek {h['repeeks']}"
              f"{'   [notable]' if h['notable'] else ''}")

    print("\nPHASES")
    for ph, p in a["phase_profile"].items():
        print(f"  {ph:<11} {p['deaths']} deaths, {p['untraded']} untraded, "
              f"{p['man_down']} while down, {p['isolated']} isolated")

    print("\nISOLATION")
    for k, v in a["isolation_profile"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
