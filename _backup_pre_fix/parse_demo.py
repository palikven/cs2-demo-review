#!/usr/bin/env python3
"""
CS2 Team Coach - Demo Parser
Parses a FACEIT (64-tick) CS2 demo into a compact JSON the viewer + AI layer read.

Usage:
    python parse_demo.py path/to/match.dem
    python parse_demo.py path/to/match.dem --pov "YourFaceitName"

Output:
    data/<demo_name>.json   (positions + events + per-round + stats)

Design notes
------------
- We SAMPLE positions (default every 128 ticks = ~0.5s at 64-tick) so the JSON
  stays small enough to load in a browser. Playback interpolates between samples.
- Stats are DETERMINISTIC. The AI layer (stage 3) only ever narrates these
  numbers; it never sees raw coordinates. That's deliberate - it's the only way
  to stop an LLM inventing tactical nonsense.
- Written defensively: FACEIT demos occasionally name columns slightly
  differently from HLTV pro demos, so we resolve column names at runtime and
  fail loud with a clear message rather than a cryptic KeyError.
"""

import argparse
import json
import sys
from pathlib import Path

import polars as pl
from awpy import Demo

# ~0.5s between position samples at 64-tick. Lower = smoother + bigger file.
SAMPLE_EVERY_TICKS = 32
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


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


# --------------------------------------------------------------------------
# main parse
# --------------------------------------------------------------------------
def parse(demo_path: Path, pov_name: str | None):
    print(f"[*] Parsing {demo_path.name} ...")
    dem = Demo(str(demo_path), verbose=False)
    dem.parse()

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
    for opt in ("z", "yaw", "health", "team", "round", "alive"):
        if cols[opt]:
            keep.append(cols[opt])
    frame_rows = sampled.select(keep).sort(cols["tick"]).to_dicts()

    # group into { tick: [ {player}, ... ] }
    frames = {}
    for r in frame_rows:
        t = r[cols["tick"]]
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
        frames.setdefault(t, []).append(entry)

    frame_list = [{"tick": t, "players": p} for t, p in sorted(frames.items())]

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
        "ax": first_present(kills_df, ["attacker_X", "attacker_x"]),
        "ay": first_present(kills_df, ["attacker_Y", "attacker_y"]),
        "vx": first_present(kills_df, ["victim_X", "victim_x", "user_X"]),
        "vy": first_present(kills_df, ["victim_Y", "victim_y", "user_Y"]),
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
        }
        if kcols["ax"] and r.get(kcols["ax"]) is not None:
            ev["ax"] = round(float(r[kcols["ax"]]), 1)
            ev["ay"] = round(float(r[kcols["ay"]]), 1)
        if kcols["vx"] and r.get(kcols["vx"]) is not None:
            ev["vx"] = round(float(r[kcols["vx"]]), 1)
            ev["vy"] = round(float(r[kcols["vy"]]), 1)
        kill_events.append(ev)

    # ---- STATS (deterministic) ----
    stats = compute_stats(kill_events, round_rows, player_list, pov_name, kcols)

    out = {
        "map": map_name,
        "tickrate": header.get("tickrate") or 64,
        "sample_every": SAMPLE_EVERY_TICKS,
        "players": player_list,
        "rounds": round_rows,
        "kills": kill_events,
        "frames": frame_list,
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
def compute_stats(kills, rounds, players, pov, kcols):
    """
    Deterministic per-player stats. Everything here is checkable and true.
    The AI layer narrates ONLY these - never raw positions.
    """
    names = [p["name"] for p in players if p["name"]]
    per = {n: {
        "kills": 0, "deaths": 0, "assists": 0, "hs_kills": 0,
        "flash_assists": 0, "entry_attempts": 0, "entry_success": 0,
        "opening_deaths": 0, "traded_deaths": 0, "untraded_deaths": 0,
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
        # opening duel = first kill of the round
        opener = ks[0]
        if opener.get("attacker") in per:
            per[opener["attacker"]]["entry_attempts"] += 1
            per[opener["attacker"]]["entry_success"] += 1
        if opener.get("victim") in per:
            per[opener["victim"]]["entry_attempts"] += 1
            per[opener["victim"]]["opening_deaths"] += 1

        # trade detection: was the victim's killer killed within ~320 ticks (~5s)?
        for i, k in enumerate(ks):
            victim = k.get("victim")
            killer = k.get("attacker")
            if victim in per:
                per[victim]["deaths"] += 1
                traded = False
                for later in ks[i + 1:]:
                    if later["tick"] - k["tick"] <= 320 and later.get("victim") == killer:
                        traded = True
                        break
                if traded:
                    per[victim]["traded_deaths"] += 1
                else:
                    per[victim]["untraded_deaths"] += 1

        for k in ks:
            a = k.get("attacker")
            if a in per:
                per[a]["kills"] += 1
                if k.get("hs"):
                    per[a]["hs_kills"] += 1
            asr = k.get("assister")
            if asr in per:
                per[asr]["assists"] += 1
                if k.get("flash_assist"):
                    per[asr]["flash_assists"] += 1

    # derived rates
    for n, s in per.items():
        s["hs_pct"] = round(100 * s["hs_kills"] / s["kills"], 1) if s["kills"] else 0.0
        s["entry_win_pct"] = (
            round(100 * s["entry_success"] / s["entry_attempts"], 1)
            if s["entry_attempts"] else 0.0
        )
        s["kd"] = round(s["kills"] / s["deaths"], 2) if s["deaths"] else float(s["kills"])

    return {
        "rounds_played": len({k.get("round") for k in kills if k.get("round") is not None}),
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
