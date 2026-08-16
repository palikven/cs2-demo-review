#!/usr/bin/env python3
"""
CS2 Team Coach - Demo Parser
Parses a FACEIT (64-tick) CS2 demo into a compact JSON the viewer + AI layer read.

Usage:
    python parse_demo.py path/to/match.dem
    python parse_demo.py path/to/match.dem --pov "YourFaceitName"

Output:
    data/<demo_name>.json   (positions + callouts + events + per-round + stats)

Design notes
------------
- We SAMPLE positions (default every 32 ticks = ~0.5s at 64-tick) so the JSON
  stays small enough to load in a browser. Playback interpolates between samples.
- Stats are DETERMINISTIC. The AI layer (stage 3) only ever narrates these
  numbers; it never sees raw coordinates. That's deliberate - it's the only way
  to stop an LLM inventing tactical nonsense. Callout NAMES are the one
  positional thing it does see, and only because Valve assigned them - "Palace"
  is a fact from the demo, not a coordinate the model has to interpret.
- Written defensively: FACEIT demos occasionally name columns slightly
  differently from HLTV pro demos, so we resolve column names at runtime and
  fail loud with a clear message rather than a cryptic KeyError.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

import polars as pl
from awpy import Demo

# ~0.5s between position samples at 64-tick. Lower = smoother + bigger file.
SAMPLE_EVERY_TICKS = 32
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

# awpy always parses ["last_place_name","X","Y","Z","health","team_name"] and
# nothing else unless you ask. Everything below is in awpy's DEFAULT_PLAYER_PROPS
# but only applies if passed explicitly - without it the viewer's view cones are
# dead code, because no demo we parse carries yaw.
#
# Deliberately NOT requested: active_weapon. It parses fine but the value is a
# numeric entity id (1392828.0), not a weapon name - useless without a lookup
# table we don't have. `inventory` carries real weapon names instead; we keep it
# off the frames (a list per player per tick would dwarf the rest of the file)
# and read it only on kill events.
EXTRA_PLAYER_PROPS = ["yaw", "pitch", "armor_value", "inventory",
                      "flash_duration", "is_alive"]

# A callout label drawn from fewer samples than this is somebody clipping a
# corner, not a place anyone played. Drawing it just litters the radar.
MIN_PLACE_SAMPLES = 20


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def first_present(df: pl.DataFrame, candidates):
    """Return the first column name that actually exists in df, else None."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def to_dicts(df: pl.DataFrame):
    """Polars -> list[dict], safe on empty frames."""
    if df is None or df.height == 0:
        return []
    return df.to_dicts()


def num(v, default=0):
    return default if v is None else v


def clean_place(v):
    """
    Normalise a callout name, or None if the demo doesn't have one here.

    Valve emits an empty string - not null - for a nav area with no place name
    assigned, so a plain `is not None` check lets "" through and you end up with
    a nameless entry in place_names and a label drawn at the median of every
    unnamed corner on the map.
    """
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# --------------------------------------------------------------------------
# main parse
# --------------------------------------------------------------------------
def parse(demo_path: Path, pov_name: str | None):
    print(f"[*] Parsing {demo_path.name} ...")
    dem = Demo(str(demo_path), verbose=False)
    dem.parse(player_props=EXTRA_PLAYER_PROPS)

    header = dem.header or {}
    map_name = header.get("map_name") or header.get("map") or "unknown"
    print(f"[*] Map: {map_name}")

    rounds_df = dem.rounds
    kills_df = dem.kills
    ticks_df = dem.ticks

    if ticks_df is None or ticks_df.height == 0:
        sys.exit("[!] No tick data parsed - is this a valid CS2 demo?")

    # ---- resolve column names once (defensive against schema drift) ----
    cols = {
        "tick": first_present(ticks_df, ["tick"]),
        "name": first_present(ticks_df, ["name", "player_name"]),
        "steamid": first_present(ticks_df, ["steamid", "steam_id", "user_steamid"]),
        "x": first_present(ticks_df, ["X", "x"]),
        "y": first_present(ticks_df, ["Y", "y"]),
        "z": first_present(ticks_df, ["Z", "z"]),
        "yaw": first_present(ticks_df, ["yaw", "view_angle_x", "pitch_yaw_y"]),
        "health": first_present(ticks_df, ["health"]),
        "team": first_present(ticks_df, ["team_name", "team_num", "team", "side"]),
        "round": first_present(ticks_df, ["round_num", "round", "total_rounds_played"]),
        "alive": first_present(ticks_df, ["is_alive", "alive"]),
        # Valve's own callout name for the nav area a player is standing in.
        # awpy parses last_place_name on every tick whether you ask or not and
        # renames it to `place` (parsers/utils.py fix_common_names). We used to
        # select it into `keep` and then never write it out.
        "place": first_present(ticks_df, ["place", "last_place_name"]),
    }
    missing = [k for k in ("tick", "name", "x", "y") if cols[k] is None]
    if missing:
        print("[!] Available tick columns:", ticks_df.columns)
        sys.exit(f"[!] Missing required columns: {missing}. See list above and tell me.")

    print(f"[*] Using columns: {cols}")

    # ---- downsample ticks ----
    sampled = ticks_df.filter(
        (pl.col(cols["tick"]) % SAMPLE_EVERY_TICKS) == 0
    )
    print(f"[*] {ticks_df.height:,} raw ticks -> {sampled.height:,} sampled rows")

    # ---- build frames grouped by tick ----
    # figure out which players exist
    players = (
        ticks_df.select([cols["name"], cols["steamid"]] if cols["steamid"] else [cols["name"]])
        .unique()
    )
    player_list = []
    for row in players.to_dicts():
        player_list.append({
            "name": row.get(cols["name"]),
            "steamid": str(row.get(cols["steamid"])) if cols["steamid"] else row.get(cols["name"]),
        })

    # per-tick frame rows
    keep = [cols["tick"], cols["name"], cols["x"], cols["y"]]
    for opt in ("z", "yaw", "health", "team", "round", "alive", "place"):
        if cols[opt]:
            keep.append(cols[opt])
    frame_rows = sampled.select(keep).sort(cols["tick"]).to_dicts()

    # Callout names repeat across ~40k position samples, so store each one once
    # in place_names[] and reference it by index. Interning costs a dict lookup
    # per row and saves roughly a third of the frame payload.
    place_names = []
    place_index = {}
    place_points = {}       # index -> ([x...], [y...]) for the label centroids

    # group into { tick: [ {player}, ... ] }
    frames = {}
    frame_round = {}        # tick -> round_num, shared by every player at that tick
    skipped_null = 0
    for r in frame_rows:
        t = r[cols["tick"]]
        # A player who is disconnected, reconnecting, or not yet spawned has no
        # entity in the world, so the parser emits a row with null coordinates
        # (and null health/side). There is nothing to draw - skip the row rather
        # than crash on float(None). Seen in 2v5.dem round 5, where two players
        # dropped: 850 raw rows, 26 of them on sampled ticks.
        if r[cols["x"]] is None or r[cols["y"]] is None:
            skipped_null += 1
            continue
        entry = {
            "n": r[cols["name"]],
            "x": round(float(r[cols["x"]]), 1),
            "y": round(float(r[cols["y"]]), 1),
        }
        if cols["yaw"] and r.get(cols["yaw"]) is not None:
            entry["yaw"] = round(float(r[cols["yaw"]]), 1)
        if cols["health"] and r.get(cols["health"]) is not None:
            entry["hp"] = int(num(r[cols["health"]]))
        if cols["team"] and r.get(cols["team"]) is not None:
            entry["t"] = str(r[cols["team"]])
        if cols["alive"] and r.get(cols["alive"]) is not None:
            entry["a"] = bool(r[cols["alive"]])
        if cols["z"] and r.get(cols["z"]) is not None:
            entry["z"] = round(float(r[cols["z"]]), 1)
        if cols["place"]:
            place = clean_place(r.get(cols["place"]))
            if place is not None:
                idx = place_index.get(place)
                if idx is None:
                    idx = len(place_names)
                    place_index[place] = idx
                    place_names.append(place)
                    place_points[idx] = ([], [])
                entry["pl"] = idx
                place_points[idx][0].append(entry["x"])
                place_points[idx][1].append(entry["y"])
        # Round number is a property of the tick, not of each player standing on
        # it, so it goes on the frame rather than being repeated ten times.
        if cols["round"] and r.get(cols["round"]) is not None:
            frame_round[t] = int(num(r[cols["round"]]))
        frames.setdefault(t, []).append(entry)

    frame_list = []
    for t, p in sorted(frames.items()):
        fr = {"tick": t, "players": p}
        if t in frame_round:
            fr["round"] = frame_round[t]
        frame_list.append(fr)
    if skipped_null:
        print(f"[*] Skipped {skipped_null:,} sampled rows with no position "
              f"(disconnected / unspawned players)")

    # ---- callout label positions ----
    # The label for "Palace" goes at the MEDIAN of every position sample taken
    # there, not the mean. Callout regions are routinely L-shaped or wrap a
    # corner, and the mean of an L lands in the wall between its arms; the
    # median stays on a spot somebody actually stood.
    places = {}
    for idx, (xs, ys) in place_points.items():
        if len(xs) < MIN_PLACE_SAMPLES:
            continue
        places[place_names[idx]] = {
            "x": round(statistics.median(xs), 1),
            "y": round(statistics.median(ys), 1),
            "n": len(xs),
        }
    if place_names:
        print(f"[*] {len(place_names)} callouts seen, {len(places)} with "
              f">={MIN_PLACE_SAMPLES} samples to label")

    # ---- rounds ----
    round_rows = to_dicts(rounds_df)

    # ---- kills (for viewer event markers + stats) ----
    kill_events = []
    kcols = {
        "tick": first_present(kills_df, ["tick"]),
        "attacker": first_present(kills_df, ["attacker_name", "attacker"]),
        "victim": first_present(kills_df, ["victim_name", "victim", "user_name"]),
        "weapon": first_present(kills_df, ["weapon"]),
        "hs": first_present(kills_df, ["headshot", "is_headshot"]),
        "round": first_present(kills_df, ["round_num", "round", "total_rounds_played"]),
        "assist": first_present(kills_df, ["assister_name", "assister"]),
        "flash": first_present(kills_df, ["assistedflash", "assist_flash", "flash_assist"]),
        "aside": first_present(kills_df, ["attacker_side", "attacker_team_name", "attacker_team"]),
        "vside": first_present(kills_df, ["victim_side", "victim_team_name", "victim_team"]),
        "ax": first_present(kills_df, ["attacker_X", "attacker_x"]),
        "ay": first_present(kills_df, ["attacker_Y", "attacker_y"]),
        "vx": first_present(kills_df, ["victim_X", "victim_x", "user_X"]),
        "vy": first_present(kills_df, ["victim_Y", "victim_y", "user_Y"]),
        "az": first_present(kills_df, ["attacker_Z", "attacker_z"]),
        "vz": first_present(kills_df, ["victim_Z", "victim_z", "user_Z"]),
        # The whole point of the coaching layer: which callout each side of the
        # duel was standing in. awpy prefixes the tick-level `place` onto kills.
        "aplace": first_present(kills_df, ["attacker_place", "attacker_last_place_name"]),
        "vplace": first_present(kills_df, ["victim_place", "victim_last_place_name"]),
        # Engagement context awpy computes for free and we were ignoring.
        "dist": first_present(kills_df, ["distance"]),
        "blind": first_present(kills_df, ["attackerblind"]),
        "smoke": first_present(kills_df, ["thrusmoke"]),
        "air": first_present(kills_df, ["attackerinair"]),
        "noscope": first_present(kills_df, ["noscope"]),
        "wallbang": first_present(kills_df, ["penetrated"]),
        "ainv": first_present(kills_df, ["attacker_inventory"]),
        "vinv": first_present(kills_df, ["victim_inventory"]),
    }
    for r in to_dicts(kills_df):
        ev = {
            "tick": r.get(kcols["tick"]),
            "round": r.get(kcols["round"]) if kcols["round"] else None,
            "attacker": r.get(kcols["attacker"]) if kcols["attacker"] else None,
            "victim": r.get(kcols["victim"]) if kcols["victim"] else None,
            "weapon": r.get(kcols["weapon"]) if kcols["weapon"] else None,
            "hs": bool(r.get(kcols["hs"])) if kcols["hs"] else False,
            "assister": r.get(kcols["assist"]) if kcols["assist"] else None,
            "flash_assist": bool(r.get(kcols["flash"])) if kcols["flash"] else False,
            # sides are needed to tell a real trade from "the killer happened to
            # die" - see is_trade() in compute_stats
            "attacker_side": r.get(kcols["aside"]) if kcols["aside"] else None,
            "victim_side": r.get(kcols["vside"]) if kcols["vside"] else None,
        }
        if kcols["ax"] and r.get(kcols["ax"]) is not None:
            ev["ax"] = round(float(r[kcols["ax"]]), 1)
            ev["ay"] = round(float(r[kcols["ay"]]), 1)
        if kcols["vx"] and r.get(kcols["vx"]) is not None:
            ev["vx"] = round(float(r[kcols["vx"]]), 1)
            ev["vy"] = round(float(r[kcols["vy"]]), 1)
        if kcols["az"] and r.get(kcols["az"]) is not None:
            ev["az"] = round(float(r[kcols["az"]]), 1)
        if kcols["vz"] and r.get(kcols["vz"]) is not None:
            ev["vz"] = round(float(r[kcols["vz"]]), 1)

        # Places are the coaching layer's whole vocabulary - emit them only when
        # actually named, so downstream code can treat absence as "unknown"
        # rather than having to special-case an empty string.
        aplace = clean_place(r.get(kcols["aplace"])) if kcols["aplace"] else None
        vplace = clean_place(r.get(kcols["vplace"])) if kcols["vplace"] else None
        if aplace:
            ev["attacker_place"] = aplace
        if vplace:
            ev["victim_place"] = vplace

        if kcols["dist"] and r.get(kcols["dist"]) is not None:
            ev["dist"] = round(float(r[kcols["dist"]]), 1)
        for key, col in (("blind", "blind"), ("smoke", "smoke"),
                         ("air", "air"), ("noscope", "noscope")):
            if kcols[col] and r.get(kcols[col]) is not None:
                if bool(r[kcols[col]]):
                    ev[key] = True
        if kcols["wallbang"] and num(r.get(kcols["wallbang"]), 0):
            ev["wallbang"] = True
        # What each side was holding. Only on kills (159 rows), never on frames -
        # an inventory list per player per tick would be larger than the rest of
        # the file put together.
        if kcols["ainv"] and r.get(kcols["ainv"]) is not None:
            ev["attacker_inv"] = [str(w) for w in r[kcols["ainv"]]]
        if kcols["vinv"] and r.get(kcols["vinv"]) is not None:
            ev["victim_inv"] = [str(w) for w in r[kcols["vinv"]]]
        kill_events.append(ev)

    # ---- damage rows (for ADR) ----
    damages_df = getattr(dem, "damages", None)
    dcols = {
        "attacker": first_present(damages_df, ["attacker_name", "attacker"]) if damages_df is not None else None,
        "victim": first_present(damages_df, ["victim_name", "victim", "user_name"]) if damages_df is not None else None,
        "aside": first_present(damages_df, ["attacker_side", "attacker_team_name"]) if damages_df is not None else None,
        "vside": first_present(damages_df, ["victim_side", "victim_team_name"]) if damages_df is not None else None,
        # dmg_health_real is capped at the victim's remaining HP; dmg_health is
        # the raw roll and over-counts on the killing blow. ADR wants the capped one.
        "dmg": first_present(damages_df, ["dmg_health_real", "dmg_health", "damage"]) if damages_df is not None else None,
    }
    damage_rows = []
    if damages_df is not None and dcols["dmg"]:
        for r in to_dicts(damages_df):
            damage_rows.append({
                "attacker": r.get(dcols["attacker"]) if dcols["attacker"] else None,
                "victim": r.get(dcols["victim"]) if dcols["victim"] else None,
                "attacker_side": r.get(dcols["aside"]) if dcols["aside"] else None,
                "victim_side": r.get(dcols["vside"]) if dcols["vside"] else None,
                "dmg": num(r.get(dcols["dmg"]), 0),
            })
        print(f"[*] {len(damage_rows):,} damage events -> ADR")
    else:
        print("[!] No damage data available - ADR will be reported as None")

    # ---- STATS (deterministic) ----
    stats = compute_stats(kill_events, round_rows, player_list, pov_name, kcols,
                          damages=damage_rows)

    out = {
        "map": map_name,
        "tickrate": header.get("tickrate") or 64,
        "sample_every": SAMPLE_EVERY_TICKS,
        "players": player_list,
        "rounds": round_rows,
        "kills": kill_events,
        "frames": frame_list,
        # place_names is the lookup table for the per-frame `pl` index; places
        # is the derived label position for each callout the viewer can draw.
        "place_names": place_names,
        "places": places,
        "stats": stats,
        "pov": pov_name,
    }

    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / (demo_path.stem + ".json")
    with open(out_path, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    size_mb = out_path.stat().st_size / 1_000_000
    print(f"[+] Wrote {out_path.name} ({size_mb:.1f} MB, {len(frame_list):,} frames)")
    return out_path


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------
TRADE_WINDOW_TICKS = 320   # ~5s at 64-tick


def is_player_kill(k):
    """
    True only for a kill one player landed on another.

    Excludes the two things a demo calls a 'kill' that nobody earned:
      - world deaths (fall damage, bomb): attacker is null
      - suicides: attacker is the victim
    Both appear in real FACEIT demos - 2v5.dem has 4 of each.
    """
    a, v = k.get("attacker"), k.get("victim")
    return a is not None and v is not None and a != v


def is_trade(kill, later_kills, window=TRADE_WINDOW_TICKS):
    """
    Was this death traded? i.e. did a TEAMMATE of the victim kill the killer
    within `window` ticks?

    The killer merely dying is not a trade - he may have fallen off the map or
    been teamkilled, and neither buys the victim's team anything. When side data
    is missing (older parses) we fall back to counting it, because the common
    case by far is that the person who avenged you was on your team.

    `later_kills` must be the round's remaining kills in tick order.
    """
    if not is_player_kill(kill):
        return False
    killer = kill.get("attacker")
    for later in later_kills:
        if later["tick"] - kill["tick"] > window:
            break
        if not is_player_kill(later) or later["victim"] != killer:
            continue
        v_side, t_side = kill.get("victim_side"), later.get("attacker_side")
        if v_side is None or t_side is None or v_side == t_side:
            return True
    return False


def compute_stats(kills, rounds, players, pov, kcols, damages=None):
    """
    Deterministic per-player stats. Everything here is checkable and true.
    The AI layer narrates ONLY these - never raw positions.
    """
    names = [p["name"] for p in players if p["name"]]
    per = {n: {
        "kills": 0, "deaths": 0, "assists": 0, "hs_kills": 0,
        "flash_assists": 0, "entry_attempts": 0, "entry_success": 0,
        "opening_deaths": 0, "traded_deaths": 0, "untraded_deaths": 0,
        "damage": 0, "adr": None,
    } for n in names}

    # group kills by round to find opening duels + trades
    by_round = {}
    for k in kills:
        by_round.setdefault(k.get("round"), []).append(k)

    for rnd, ks in by_round.items():
        ks = [k for k in ks if k.get("tick") is not None]
        ks.sort(key=lambda x: x["tick"])
        if not ks:
            continue

        # Opening duel = first PLAYER kill of the round. A round that opens with
        # someone falling off the map has not had its opening duel yet, so we
        # skip past those rather than crediting a phantom entry.
        opener = next((k for k in ks if is_player_kill(k)), None)
        if opener is not None:
            if opener["attacker"] in per:
                per[opener["attacker"]]["entry_attempts"] += 1
                per[opener["attacker"]]["entry_success"] += 1
            if opener["victim"] in per:
                per[opener["victim"]]["entry_attempts"] += 1
                per[opener["victim"]]["opening_deaths"] += 1

        # Trade detection - see is_trade(), shared with the analysis layer so
        # both agree on what counts.
        for i, k in enumerate(ks):
            victim = k.get("victim")
            if victim not in per:
                continue
            per[victim]["deaths"] += 1
            traded = is_trade(k, ks[i + 1:])
            per[victim]["traded_deaths" if traded else "untraded_deaths"] += 1

        for k in ks:
            if is_player_kill(k) and k["attacker"] in per:
                per[k["attacker"]]["kills"] += 1
                if k.get("hs"):
                    per[k["attacker"]]["hs_kills"] += 1
            asr = k.get("assister")
            if asr in per:
                per[asr]["assists"] += 1
                if k.get("flash_assist"):
                    per[asr]["flash_assists"] += 1

    # rounds_played comes from the round table, not from "rounds that had a kill" -
    # a round can end on the bomb or the clock with nobody dying.
    round_nums = {r.get("round_num") for r in (rounds or [])
                  if isinstance(r, dict) and r.get("round_num") is not None}
    rounds_played = len(round_nums) or len(rounds or []) or len(
        {k.get("round") for k in kills if k.get("round") is not None}
    )

    # ---- damage / ADR ----
    for d in (damages or []):
        a, v = d.get("attacker"), d.get("victim")
        if a not in per or a == v:
            continue
        a_side, v_side = d.get("attacker_side"), d.get("victim_side")
        if a_side is not None and v_side is not None and a_side == v_side:
            continue          # team damage does not count toward ADR
        per[a]["damage"] += num(d.get("dmg"), 0)

    # derived rates
    for n, s in per.items():
        s["hs_pct"] = round(100 * s["hs_kills"] / s["kills"], 1) if s["kills"] else 0.0
        s["entry_win_pct"] = (
            round(100 * s["entry_success"] / s["entry_attempts"], 1)
            if s["entry_attempts"] else 0.0
        )
        s["kd"] = round(s["kills"] / s["deaths"], 2) if s["deaths"] else float(s["kills"])
        s["adr"] = round(s["damage"] / rounds_played, 1) if (damages and rounds_played) else None

    return {
        "rounds_played": rounds_played,
        "per_player": per,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("demo", help="path to .dem file")
    ap.add_argument("--pov", default=None, help="your FACEIT name (optional, for focus)")
    args = ap.parse_args()

    demo_path = Path(args.demo).expanduser().resolve()
    if not demo_path.exists():
        sys.exit(f"[!] File not found: {demo_path}")
    parse(demo_path, args.pov)
