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
import math
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
# table we don't have. `inventory` carries real weapon names instead. Putting
# that list on every frame entry would dwarf the rest of the file, so distinct
# loadouts are interned once and each entry stores a single index (`eq`).
EXTRA_PLAYER_PROPS = ["yaw", "pitch", "armor_value", "inventory",
                      "flash_duration", "is_alive", "has_helmet", "has_defuser"]

# A callout label drawn from fewer samples than this is somebody clipping a
# corner, not a place anyone played. Drawing it just litters the radar.
MIN_PLACE_SAMPLES = 20

# Every grenade entity awpy reports, mapped to the short kind the viewer draws.
# Both the "Grenade" and "Projectile" spellings show up in grenade_type; only
# the projectile rows carry positions, which is what we want anyway.
GRENADE_KINDS = {
    "CSmokeGrenadeProjectile": "smoke",
    "CMolotovProjectile": "fire",
    "CFlashbangProjectile": "flash",
    "CHEGrenadeProjectile": "he",
    "CDecoyProjectile": "decoy",
}

# Throw arcs are sampled every tick, which is far finer than anyone can see on
# a 900px radar. Keep every Nth point (plus both ends) - the arc still reads as
# an arc and the payload stays small.
THROW_EVERY_TICKS = 4

# A projectile that moves less than this between consecutive ticks has come to
# rest, and everything after it is the entity sitting on the floor for the rest
# of its life. Smoke projectiles in particular persist for the full 18s burn,
# which is why 75% of all grenade rows belong to smokes.
AT_REST_UNITS = 1.0


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


def round_of(tick, round_rows):
    """Which round a loose tick belongs to. Some raw event tables have no
    round_num of their own (flashbang_detonate, hegrenade_detonate)."""
    if tick is None:
        return None
    for r in round_rows:
        start = r.get("start")
        end = r.get("official_end") or r.get("end")
        if start is not None and end is not None and start <= tick <= end:
            return r.get("round_num")
    return None


def timed_utility(rows, default_seconds, round_rows, tickrate):
    """
    Normalise a smoke/inferno table into {s,e,x,y,r,by,side} entries.

    THE trap in this whole feature lives here. dem.smokes and dem.infernos each
    carry BOTH `thrower_X/Y/Z` and `X/Y/Z`. The thrower columns are where the
    player was standing - a median of ~800 units from where the grenade
    actually landed - and `X/Y` is the detonation point. Reading the wrong pair
    still produces a perfectly well-formed table that renders happily, with
    every smoke drawn on top of whoever threw it. Nothing but a human looking
    at the map would notice, which is exactly why it's pinned by a test.

    Split out from parse_utility() so it can be tested without an awpy Demo.
    """
    out = []
    for r in rows:
        if r.get("X") is None or r.get("Y") is None:
            continue
        start = int(r["start_tick"])
        end = r.get("end_tick")
        if end is None:
            # No "expired" event: the smoke was still up when the round ended,
            # so the demo never records it going out. Fall back to its nominal
            # duration and clamp to the end of the round - without this the
            # entry is dropped, and a smoke that covered the whole retake
            # simply never renders.
            rnd = r.get("round_num")
            end = start + int(default_seconds * tickrate)
            rr = next((x for x in (round_rows or [])
                       if x.get("round_num") == rnd), None)
            if rr:
                cap = rr.get("official_end") or rr.get("end")
                if cap is not None:
                    end = min(end, int(cap))
        ev = {
            "s": start,
            "e": int(end),
            "x": round(float(r["X"]), 1),
            "y": round(float(r["Y"]), 1),
            "r": r.get("round_num"),
        }
        if r.get("thrower_name") is not None:
            ev["by"] = r["thrower_name"]
        if r.get("thrower_side") is not None:
            ev["side"] = r["thrower_side"]
        out.append(ev)
    return out


def parse_utility(dem, round_rows, side_by_round, tickrate=64):
    """Smokes, molotovs, flashes, HEs, the bomb, and the throw arcs."""
    util = {"smokes": [], "fires": [], "flashes": [], "hes": [], "throws": []}
    bomb_events = []

    # Nominal durations, used only when the demo never recorded the entity
    # expiring. CS2 smokes last ~18s; molotov/incendiary burn ~7s.
    util["smokes"] = timed_utility(to_dicts(getattr(dem, "smokes", None)),
                                   getattr(dem, "smoke_duration", 18),
                                   round_rows, tickrate)
    util["fires"] = timed_utility(to_dicts(getattr(dem, "infernos", None)),
                                  7, round_rows, tickrate)

    # Flash and HE detonations live in the raw event tables rather than a named
    # property, and those tables carry no round number - hence round_of().
    events = getattr(dem, "events", {}) or {}
    for key, out_key in (("flashbang_detonate", "flashes"),
                         ("hegrenade_detonate", "hes")):
        df = events.get(key)
        if df is None or df.height == 0:
            continue
        cols = set(df.columns)
        xk = "x" if "x" in cols else ("X" if "X" in cols else None)
        yk = "y" if "y" in cols else ("Y" if "Y" in cols else None)
        if not xk or not yk:
            continue
        for r in to_dicts(df):
            if r.get(xk) is None or r.get(yk) is None:
                continue
            ev = {"t": int(r["tick"]), "x": round(float(r[xk]), 1),
                  "y": round(float(r[yk]), 1),
                  "r": r.get("round_num") or round_of(r.get("tick"), round_rows)}
            if "user_name" in cols:
                ev["by"] = r.get("user_name")
            if "user_side" in cols:
                ev["side"] = r.get("user_side")
            util[out_key].append(ev)

    # ---- bomb ----
    bdf = getattr(dem, "bomb", None)
    if bdf is not None and bdf.height:
        for r in to_dicts(bdf):
            # pickup/drop happen constantly and tell a reviewer nothing
            if r.get("event") not in ("plant", "defuse", "detonate"):
                continue
            if r.get("X") is None:
                continue
            bomb_events.append({
                "t": int(r["tick"]), "event": r["event"],
                "x": round(float(r["X"]), 1), "y": round(float(r["Y"]), 1),
                "r": r.get("round_num"), "site": r.get("bombsite"),
                "by": r.get("name"),
            })

    # ---- throw arcs ----
    gdf = getattr(dem, "grenades", None)
    if gdf is not None and gdf.height:
        have = set(gdf.columns)
        if {"entity_id", "tick", "X", "Y", "grenade_type"} <= have:
            flown = gdf.filter(pl.col("X").is_not_null())
            group_cols = ["entity_id"] + (["round_num"] if "round_num" in have else [])
            for _, sub in flown.group_by(group_cols):
                rows = sub.sort("tick").to_dicts()
                kind = GRENADE_KINDS.get(rows[0].get("grenade_type"))
                if kind is None:
                    continue
                # trim to the airborne part - see AT_REST_UNITS
                flight = [rows[0]]
                for p in rows[1:]:
                    prev = flight[-1]
                    moved = math.dist(
                        (p["X"], p["Y"], p.get("Z") or 0.0),
                        (prev["X"], prev["Y"], prev.get("Z") or 0.0))
                    if moved < AT_REST_UNITS:
                        break
                    flight.append(p)
                if len(flight) < 2:
                    continue
                pts = [p for i, p in enumerate(flight)
                       if i % THROW_EVERY_TICKS == 0]
                if pts[-1] is not flight[-1]:
                    pts.append(flight[-1])
                rnd = rows[0].get("round_num")
                thrower = rows[0].get("thrower")
                util["throws"].append({
                    "kind": kind,
                    "s": int(flight[0]["tick"]),
                    "e": int(flight[-1]["tick"]),
                    "r": rnd,
                    "by": thrower,
                    "side": (side_by_round.get(rnd) or {}).get(thrower),
                    "p": [[round(float(p["X"]), 1), round(float(p["Y"]), 1)] for p in pts],
                })
            util["throws"].sort(key=lambda t: t["s"])

    counts = ", ".join(f"{k} {len(v)}" for k, v in util.items() if v)
    print(f"[*] Utility: {counts or 'none found'}, bomb events {len(bomb_events)}")
    return util, bomb_events


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
        # Loadout. awpy renames armor_value -> armor in fix_common_names.
        # `inventory` is a list of display names ("AK-47", "Smoke Grenade").
        "armor": first_present(ticks_df, ["armor", "armor_value"]),
        "helmet": first_present(ticks_df, ["has_helmet"]),
        "defuser": first_present(ticks_df, ["has_defuser"]),
        "inventory": first_present(ticks_df, ["inventory"]),
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
    for opt in ("z", "yaw", "health", "team", "round", "alive", "place",
                "armor", "helmet", "defuser", "inventory"):
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

    # Loadout interning: item_names holds every distinct item string, loadouts
    # holds each distinct combination as a list of indices into it.
    item_names, item_ids = [], {}
    loadouts, loadout_index = [], {}

    def item_id(name):
        idx = item_ids.get(name)
        if idx is None:
            idx = len(item_names)
            item_ids[name] = idx
            item_names.append(name)
        return idx
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
        if cols["armor"] and r.get(cols["armor"]) is not None:
            armor = int(num(r[cols["armor"]]))
            if armor:
                entry["ar"] = armor
        if cols["helmet"] and r.get(cols["helmet"]):
            entry["hm"] = 1
        if cols["defuser"] and r.get(cols["defuser"]):
            entry["df"] = 1
        if cols["inventory"]:
            # A loadout changes on a buy or a pickup, so across 40,000 player
            # samples there are only a few hundred distinct ones. Interning the
            # whole loadout costs one integer per player per frame; storing the
            # item list inline would roughly double the file.
            inv = r.get(cols["inventory"]) or []
            key = tuple(sorted(str(i) for i in inv))
            idx = loadout_index.get(key)
            if idx is None:
                idx = len(loadouts)
                loadout_index[key] = idx
                loadouts.append([item_id(i) for i in key])
            if loadouts[idx]:
                entry["eq"] = idx
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
    if item_names:
        print(f"[*] {len(item_names)} distinct items in "
              f"{len(loadouts)} distinct loadouts")

    # ---- rounds ----
    round_rows = to_dicts(rounds_df)

    # name -> side, per round. Sides swap at half time, so a single global map
    # would mislabel half the match. Built from the frames we already have
    # rather than re-scanning the tick table.
    side_by_round = {}
    for fr in frame_list:
        rnd = fr.get("round")
        if rnd is None or rnd in side_by_round:
            continue
        side_by_round[rnd] = {p["n"]: p.get("t") for p in fr["players"] if p.get("t")}

    # ---- utility ----
    utility, bomb_events = parse_utility(
        dem, round_rows, side_by_round, header.get("tickrate") or 64)

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
        # item_names is the lookup table for loadouts; each frame entry's `eq`
        # indexes into loadouts, which indexes into item_names.
        "item_names": item_names,
        "loadouts": loadouts,
        # Price of the most expensive weapon in each loadout, parallel to
        # `loadouts`. A player's equipment value is this plus their armour,
        # which is already on the frame entry - so it stays one number per
        # distinct loadout rather than one per player per frame.
        "loadout_values": [
            max((WEAPON_PRICES.get(item_names[i], 0) for i in l), default=0)
            for l in loadouts
        ],
        "armor_prices": {"vest": ARMOR_PRICE, "helmet_extra": HELMET_EXTRA,
                         "full_buy": FULL_BUY_VALUE},
        "utility": utility,
        "bomb": bomb_events,
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

# ---- Equipment value ------------------------------------------------------
# HLTV's Rating 3.0 eco adjustment is defined as "the price of a player's armor
# plus their most expensive weapon", so that is what this computes rather than
# the game's own current_equip_value (which also counts the pistol and every
# grenade, and so rates a full-utility rifler well above a bare AWPer).
#
# CS2 buy-menu prices. Only the weapons that can be a player's most expensive
# are worth listing; anything unknown scores 0 and falls back to the armour,
# which is the safe direction - it under-rates rather than inventing value.
WEAPON_PRICES = {
    # rifles and snipers
    "AK-47": 2700, "M4A4": 3000, "M4A1-S": 2900, "AWP": 4750,
    "Galil AR": 1800, "FAMAS": 2050, "AUG": 3300, "SG 553": 3000,
    "SSG 08": 1700, "G3SG1": 5000, "SCAR-20": 5000,
    # SMGs
    "MAC-10": 1050, "MP9": 1250, "MP7": 1500, "MP5-SD": 1500,
    "UMP-45": 1200, "P90": 2350, "PP-Bizon": 1400,
    # heavy
    "Nova": 1050, "XM1014": 2000, "MAG-7": 1300, "Sawed-Off": 1100,
    "M249": 5200, "Negev": 1700,
    # pistols
    "Glock-18": 200, "USP-S": 200, "P2000": 200, "P250": 300,
    "Five-SeveN": 500, "Tec-9": 500, "CZ75-Auto": 500,
    "Dual Berettas": 300, "Desert Eagle": 700, "R8 Revolver": 600,
    "Zeus x27": 200,
}
ARMOR_PRICE = 650          # kevlar
HELMET_EXTRA = 350         # kevlar + helmet is 1000 total
FULL_BUY_VALUE = 3700      # rifle + kevlar&helmet, the reference "full buy"


def equipment_value(items, armor, helmet):
    """Armor price plus the most expensive weapon, per HLTV's stated rule."""
    best = 0
    for it in items or ():
        best = max(best, WEAPON_PRICES.get(it, 0))
    kit = 0
    if armor:
        kit = ARMOR_PRICE + (HELMET_EXTRA if helmet else 0)
    return best + kit


# ---- HLTV Rating ----------------------------------------------------------
# Rating 1.0 is HLTV's published formula and these are its published league
# averages, so the number we produce is the real thing rather than a lookalike.
AVERAGE_KPR = 0.679        # kills per round
AVERAGE_SPR = 0.317        # survivals per round
AVERAGE_RMK = 1.277        # the weighted multi-kill term below
# 1K counts 1, 2K counts 4, 3K counts 9 ... i.e. n^2, which is what makes the
# metric reward the round-winning multi-kill over four scattered singles.
MULTI_KILL_WEIGHTS = {1: 1, 2: 4, 3: 9, 4: 16, 5: 25}

# Rating 2.0's formula was never published; these coefficients are the widely
# used community regression against the numbers HLTV used to display.
#
# It is doubly removed from anything current: HLTV replaced 2.0 with 2.1, and
# then in August 2025 with Rating 3.0, whose largest component is Round Swing
# (~33%) and whose sub-rating internals are proprietary. So this estimates a
# formula that is both unpublished AND retired - kept only for comparison with
# archived HLTV pages, exported under a name that says so, and never to be
# quoted as HLTV's figure. Rating 1.0 above is the one defensible number here;
# for impact, use the swing model in swing.py, which is at least ours and
# documented.
R2 = {"kast": 0.0073, "kpr": 0.3591, "dpr": -0.5329,
      "impact": 0.2372, "adr": 0.0032, "const": 0.1587}
IMPACT = {"kpr": 2.13, "apr": 0.42, "const": -0.41}


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

    # Per-round bookkeeping for KAST and the multi-kill term. Kept separate
    # from `per` because these are counts of ROUNDS, not of events: four kills
    # in one round is one entry here and four in `per[n]["kills"]`.
    kast_rounds = {n: set() for n in names}      # rounds with a K, A, S or T
    multi = {n: {} for n in names}               # round -> kills in that round

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
        died_this_round = set()
        for i, k in enumerate(ks):
            victim = k.get("victim")
            if victim not in per:
                continue
            per[victim]["deaths"] += 1
            died_this_round.add(victim)
            traded = is_trade(k, ks[i + 1:])
            per[victim]["traded_deaths" if traded else "untraded_deaths"] += 1
            # the T in KAST: dying is survivable for the round's purposes if a
            # teammate trades you
            if traded:
                kast_rounds[victim].add(rnd)

        for k in ks:
            if is_player_kill(k) and k["attacker"] in per:
                per[k["attacker"]]["kills"] += 1
                multi[k["attacker"]][rnd] = multi[k["attacker"]].get(rnd, 0) + 1
                kast_rounds[k["attacker"]].add(rnd)          # K
                if k.get("hs"):
                    per[k["attacker"]]["hs_kills"] += 1
            asr = k.get("assister")
            if asr in per:
                per[asr]["assists"] += 1
                kast_rounds[asr].add(rnd)                    # A
                if k.get("flash_assist"):
                    per[asr]["flash_assists"] += 1

        # S: anyone who was not a victim this round lived through it
        for n in names:
            if n not in died_this_round:
                kast_rounds[n].add(rnd)

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

    # A round with no kills at all never appears in by_round, but everybody
    # lived through it - so it is a KAST round for all ten players. Without
    # this, a slow eco or a full save silently drags everyone's KAST down.
    all_rounds = {r.get("round_num") for r in (rounds or [])
                  if isinstance(r, dict) and r.get("round_num") is not None}
    for rnd in all_rounds - set(by_round):
        for n in names:
            kast_rounds[n].add(rnd)

    # derived rates
    for n, s in per.items():
        s["hs_pct"] = round(100 * s["hs_kills"] / s["kills"], 1) if s["kills"] else 0.0
        s["entry_win_pct"] = (
            round(100 * s["entry_success"] / s["entry_attempts"], 1)
            if s["entry_attempts"] else 0.0
        )
        s["kd"] = round(s["kills"] / s["deaths"], 2) if s["deaths"] else float(s["kills"])
        s["adr"] = round(s["damage"] / rounds_played, 1) if (damages and rounds_played) else None

    # ---- HLTV ratings ----
    # After the derived rates, because Rating 2.0 takes ADR as an input.
    for n, s in per.items():
        r = rounds_played or 0
        if not r:
            s["kast"] = s["kpr"] = s["dpr"] = s["apr"] = 0.0
            s["impact"] = s["rating"] = 0.0
            s["rating2_legacy_est"] = None
            continue

        s["kast"] = round(100 * len(kast_rounds[n]) / r, 1)
        s["kpr"] = round(s["kills"] / r, 3)
        s["dpr"] = round(s["deaths"] / r, 3)
        s["apr"] = round(s["assists"] / r, 3)

        # Rating 1.0 - HLTV's published formula, so this is the real number.
        kill_rating = (s["kills"] / r) / AVERAGE_KPR
        survival_rating = ((r - s["deaths"]) / r) / AVERAGE_SPR
        weighted = sum(MULTI_KILL_WEIGHTS.get(c, 0) for c in multi[n].values())
        rmk_rating = (weighted / r) / AVERAGE_RMK
        s["rating"] = round(
            (kill_rating + 0.7 * survival_rating + rmk_rating) / 2.7, 3)

        # Rating 2.0 - ESTIMATED, see the note on R2. ADR is one of its inputs,
        # so with no damage data the estimate is not meaningful; return None
        # rather than a confidently wrong number.
        s["impact"] = round(IMPACT["kpr"] * s["kpr"]
                            + IMPACT["apr"] * s["apr"] + IMPACT["const"], 3)
        if s["adr"] is None:
            s["rating2_legacy_est"] = None
        else:
            s["rating2_legacy_est"] = round(
                R2["kast"] * s["kast"] + R2["kpr"] * s["kpr"]
                + R2["dpr"] * s["dpr"] + R2["impact"] * s["impact"]
                + R2["adr"] * s["adr"] + R2["const"], 3)

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
