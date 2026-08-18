#!/usr/bin/env python3
"""
CS2 Coach - one player across every demo you have parsed.

This is what the rest of the project has been waiting for. gates.py exists
because a single match cannot support most of the claims worth making: three
deaths in one callout out of sixteen is not a habit, it is a Tuesday. Pool ten
matches and the same three-per-match becomes thirty out of a hundred and sixty,
which is a habit with a number attached.

So the gates do not change. The SAMPLE does.

POOL THE COUNTS, NEVER AVERAGE THE RATES
----------------------------------------
The one arithmetic trap here, and it is easy to get wrong in a way that looks
right. A player who died once in match A while holding utility (100%) and twenty
times in match B holding it ten times (50%) has not got a 75% rate. They have 11
of 21, which is 52%.

Averaging per-match percentages weights a match with one death the same as a
match with twenty. Every rate in this module is therefore computed from summed
numerators and summed denominators, and the per-match series is kept only for
showing DIRECTION, never for computing the total.

IDENTITY IS THE STEAMID
-----------------------
Names change between matches - people rename constantly, and FACEIT nicknames
are not stable. Every join here is on steamid; the display name is whatever they
were called most recently.

ORDERING, AND ITS LIMIT
-----------------------
Trends need the matches in order and the parsed JSON carries no match date. The
index falls back to the file's modification time, which is when it was PARSED,
not when it was played. Parse a year-old demo today and it sorts last. The order
is therefore reported alongside the trend so it can be checked, and `order` can
be passed explicitly.

WHAT IS NOT HERE
----------------
No skill rating, no percentile against other players' data, no prediction. This
pools your own matches and applies the same evidence bar to a bigger sample.
"""

import collections
import json
import statistics
from pathlib import Path

from gates import reliable

# Below this many matches, direction is not a trend - it is two points and a
# straight line through them.
MIN_MATCHES_FOR_TREND = 4

# A callout has to show up in this share of matches before it is a habit rather
# than one bad night that happens to dominate the pool.
MIN_MATCH_SHARE = 0.34


def find_matches(data_dir):
    """
    Every parsed match JSON in a directory, oldest first.

    Sidecars are excluded by suffix rather than by peeking inside - an
    `_analysis.json` has a `deaths` key and would otherwise read as a match with
    no rounds.
    """
    data_dir = Path(data_dir)
    out = []
    for p in sorted(data_dir.glob("*.json")):
        if any(p.stem.endswith(s) for s in
               ("_analysis", "_voice", "_coach", "_notes")):
            continue
        if p.stem.startswith("_"):
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime)


def identify(match, steamid=None, name=None):
    """Resolve a player in one match to (steamid, name), or None."""
    for p in match.get("players") or []:
        if steamid and str(p.get("steamid")) == str(steamid):
            return str(p["steamid"]), p.get("name")
        if name and p.get("name") == name:
            return str(p.get("steamid")), name
    return None


def _pool(rows, keys):
    """Sum a list of dicts over `keys`. Missing values count as zero."""
    total = {k: 0 for k in keys}
    for r in rows:
        for k in keys:
            v = r.get(k)
            if isinstance(v, (int, float)):
                total[k] += v
    return total


def _rate(numerator, denominator):
    return round(numerator / denominator, 3) if denominator else None


def _trend(series):
    """
    Direction over the per-match series, or None if there is not enough of it.

    First half against second half rather than a fitted line: with five or six
    noisy points a regression slope reports a direction with a confidence the
    data does not have, and nobody reads the error bar.
    """
    vals = [v for v in series if v is not None]
    if len(vals) < MIN_MATCHES_FOR_TREND:
        return None
    half = len(vals) // 2
    early, late = vals[:half], vals[half:]
    delta = statistics.mean(late) - statistics.mean(early)
    return {
        "early": round(statistics.mean(early), 3),
        "late": round(statistics.mean(late), 3),
        "delta": round(delta, 3),
        "matches": len(vals),
    }


def career(analyses, steamid):
    """
    Pool a player's per-match analyses.

    `analyses` is [(label, analysis_dict), ...] in the order the matches were
    played. Each analysis is what analyze.analyze() returns.
    """
    mine = [(label, a) for label, a in analyses if a]
    if not mine:
        return {}

    labels = [label for label, _ in mine]
    n = len(mine)

    # ---- deaths by callout, pooled ----
    place_deaths = collections.Counter()
    place_matches = collections.Counter()
    total_deaths = 0
    for _, a in mine:
        seen = set()
        for d in a.get("deaths") or []:
            total_deaths += 1
            place = d.get("victim_place")
            if place:
                place_deaths[place] += 1
                seen.add(place)
        for place in seen:
            place_matches[place] += 1

    hotspots = []
    for place, count in place_deaths.most_common():
        spread = place_matches[place] / n
        hotspots.append({
            "place": place,
            "deaths": count,
            "matches": place_matches[place],
            "match_share": round(spread, 2),
            "share_of_deaths": _rate(count, total_deaths),
            # Both bars: enough deaths to be real, and spread across enough
            # matches that one bad night cannot manufacture it.
            "notable": bool(reliable(count, total_deaths, sources=n,
                                     min_sources=1)
                            and spread >= MIN_MATCH_SHARE),
        })

    # ---- rates, pooled numerator over pooled denominator ----
    def pooled_rate(getter, num_key, den_key):
        rows = [getter(a) for _, a in mine]
        rows = [r for r in rows if r]
        if not rows:
            return None
        t = _pool(rows, (num_key, den_key))
        series = [_rate(r.get(num_key) or 0, r.get(den_key) or 0)
                  for r in rows]
        return {
            "n": t[num_key], "of": t[den_key],
            "rate": _rate(t[num_key], t[den_key]),
            "per_match": series,
            "trend": _trend(series),
            "matches": len(rows),
        }

    rates = {
        "team_flash": pooled_rate(
            lambda a: a.get("flashes"), "team_meaningful", "_flash_total"),
        "died_holding_utility": pooled_rate(
            lambda a: a.get("kit"), "deaths_holding_utility", "deaths"),
        "mic_open_after_death": pooled_rate(
            lambda a: a.get("comms"), "called_after",
            "deaths_with_time_to_call"),
    }

    # ---- simple medians ----
    aim_series = [(a.get("aim") or {}).get("median") for _, a in mine]
    aim_vals = [v for v in aim_series if v is not None]

    return {
        "steamid": str(steamid),
        "matches": n,
        "labels": labels,
        "deaths": total_deaths,
        "hotspots": hotspots,
        "rates": {k: v for k, v in rates.items() if v},
        "crosshair_height": {
            "median": round(statistics.median(aim_vals), 1) if aim_vals else None,
            "per_match": aim_series,
            "trend": _trend(aim_series),
        },
        "note": ("Rates are pooled numerator over pooled denominator, never an "
                 "average of per-match percentages - that would weight a match "
                 "with one death the same as one with twenty. `per_match` is "
                 "for direction only. Matches are ordered by FILE MODIFICATION "
                 "TIME, which is when the demo was parsed and not when it was "
                 "played, so check `labels` before reading a trend as "
                 "improvement over time."),
    }


def _flash_total(a):
    """Meaningful flashes thrown, for the team-flash denominator."""
    f = a.get("flashes") or {}
    e, t = f.get("enemy_meaningful"), f.get("team_meaningful")
    if e is None or t is None:
        return None
    return e + t


def usable_sidecar(path, player=None):
    """
    The `_analysis.json` beside `path`, or None if it cannot be trusted.

    ONE implementation on purpose. This check had two copies for about ten
    minutes - one here and one in build()'s fast path - and a mutation test
    caught that only one of them was covered. Two copies of a silent-failure
    guard is worse than none, because the tested one gives you confidence in
    the untested one.

    Pass `player` to require the sheet be for them; omit it to peek at whose it
    is, which is how build() resolves identity without opening a 5 MB match.
    """
    path = Path(path)
    side = path.with_name(path.stem + "_analysis.json")
    if not side.exists() or side.stat().st_mtime < path.stat().st_mtime:
        return None                    # missing, or the demo was re-parsed after
    try:
        a = json.loads(side.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None                    # unreadable sidecar is not fatal
    if a.get("deaths") is None:
        return None
    if player is not None and a.get("player") != player:
        return None                    # per-POV: one player's sheet is not another's
    return a


def analysis_for(path, player, match=None):
    """
    One match's analysis, from its `_analysis.json` sidecar when that is usable.

    WHY THIS IS NOT JUST analyze.analyze(): the sidecar is 47 KB and the match
    it came from is 5.1 MB. Re-analysing costs about a second per match AND
    reads the whole match file, so pooling twenty demos meant loading 100 MB and
    burning half a minute - every time a single new demo was added. The sidecar
    already holds everything career() reads, and `analyze.py --emit` already
    writes it.

    Three things make a sidecar unusable, and all three are checked rather than
    hoped for:

      * it is for a DIFFERENT player. The analysis is per-POV; a sheet built for
        one player cannot answer for another.
      * it is OLDER than the match file, so the demo was re-parsed after it was
        written and the numbers no longer describe what is on disk.
      * it is missing or unreadable.

    Any of those and it falls back to analysing from the match, which is always
    correct and merely slow.
    """
    a = usable_sidecar(path, player)
    if a is not None:
        return a, "sidecar"
    import analyze
    if match is None:
        match = analyze.load_match(path)
    return analyze.analyze(match, player), "computed"


def build(paths, steamid=None, name=None, verbose=False):
    """
    Load, pool, and report where each match's numbers came from.

    Matches are only fully LOADED when their sidecar cannot be used - the
    identity check needs the player list, which the sidecar carries as `player`,
    so a usable sidecar avoids the 5 MB read entirely.
    """
    import analyze

    rows, resolved, computed = [], None, 0
    for p in paths:
        p = Path(p)

        # Fast path: the sidecar names its own player, so identity can be
        # resolved without opening the match at all.
        a = None
        cand = usable_sidecar(p)
        if cand is not None:
            who_name = cand.get("player")
            if who_name and (name is None or who_name == name):
                # A steamid query still needs the match to confirm the id,
                # unless an earlier match already resolved it to this name.
                if not steamid or (resolved and resolved[1] == who_name):
                    a, resolved = cand, (resolved or (steamid or "", who_name))

        if a is None:
            match = analyze.load_match(p)
            who = identify(match, steamid, name)
            if not who:
                continue
            resolved = resolved or who
            a, src = analysis_for(p, who[1], match)
            if src == "computed":
                computed += 1

        if a.get("flashes"):
            a["flashes"]["_flash_total"] = _flash_total(a)
        rows.append((p.stem, a))

    if verbose:
        print(f"[*] {len(rows)} match(es); {len(rows) - computed} read from "
              f"_analysis.json, {computed} analysed from the match file")
    if not rows:
        return {}, []
    return career(rows, resolved[0] if resolved[0] else "unknown"), rows


CAREER_FILE = "career.json"


def emit(data_dir, c):
    """
    Write (or merge into) data/career.json, keyed by steamid.

    A SIDECAR RATHER THAN SOMETHING THE CHAT COMPUTES, and the reason is
    measured: analysing one match takes about a second, so a player with twenty
    parsed demos would wait twenty seconds staring at an empty chat before the
    first token streamed. The same reason _analysis.json exists.

    The cost of that choice is that the file goes stale when a new demo is
    parsed, so `generated_matches` records what it was built from and the reader
    can say so rather than silently serving old numbers.
    """
    path = Path(data_dir) / CAREER_FILE
    blob = {}
    if path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            blob = {}                      # a corrupt sidecar is not fatal
    blob[str(c["steamid"])] = dict(c, generated_matches=c["labels"])
    path.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    return path


def load(data_dir, steamid):
    """The pooled record for one player, or {}. Never raises."""
    path = Path(data_dir) / CAREER_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(str(steamid)) or {}
    except (ValueError, OSError):
        return {}


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Pool one player across every parsed demo.")
    ap.add_argument("--data", default=str(Path(__file__).resolve().parent.parent
                                          / "data"))
    ap.add_argument("--player", help="display name in any of the matches")
    ap.add_argument("--steamid")
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args()

    paths = find_matches(args.data)
    if not paths:
        print(f"No parsed matches in {args.data}.")
        return 1
    print(f"[*] {len(paths)} match(es): {', '.join(p.stem for p in paths)}")

    c, rows = build(paths, args.steamid, args.player, verbose=True)
    if not c:
        print("That player is not in any of them.")
        return 1

    print(f"\n{args.player or c['steamid']} - {c['matches']} match(es), "
          f"{c['deaths']} deaths\n")

    if c["matches"] < 2:
        print("  Only one match. Everything below is this match's numbers with "
              "a bigger label on it - parse more demos before reading any of "
              "it as a habit.\n")

    print("  WHERE THEY DIE")
    for h in c["hotspots"][:8]:
        mark = "  <- habit" if h["notable"] else ""
        print(f"    {h['place']:<14} {h['deaths']:>3} deaths in "
              f"{h['matches']}/{c['matches']} matches{mark}")

    if c["rates"]:
        print("\n  RATES (pooled, not averaged)")
        for key, r in c["rates"].items():
            line = f"    {key:<24} {r['n']:>3}/{r['of']:<4}"
            if r["rate"] is not None:
                line += f" {r['rate'] * 100:>5.0f}%"
            if r["trend"]:
                d = r["trend"]["delta"]
                line += f"   trend {d * 100:+.0f}pp"
            print(line)

    ch = c["crosshair_height"]
    if ch["median"] is not None:
        line = f"\n  Crosshair height while moving: {ch['median']}deg"
        if ch["trend"]:
            line += f" (trend {ch['trend']['delta']:+.1f}deg)"
        print(line)

    if args.emit:
        out = Path(args.data) / "career.json"
        out.write_text(json.dumps(c, indent=1), encoding="utf-8")
        print(f"\n[+] Wrote {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
