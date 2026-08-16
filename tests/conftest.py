"""
Shared fixtures for the cs2coach stats tests.

The module under test is parser/parse_demo.py. It lives outside a package, so
we put its directory on sys.path rather than restructuring the project.
"""

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PARSER_DIR = PROJECT_ROOT / "parser"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(PARSER_DIR))


@pytest.fixture(scope="session")
def stats_mod():
    """The module holding compute_stats()."""
    import parse_demo

    return parse_demo


@pytest.fixture(scope="session")
def analyze_mod():
    """The positional analysis layer."""
    import analyze

    return analyze


# ---------------------------------------------------------------------------
# synthetic builders - keep tests readable and intention-revealing
# ---------------------------------------------------------------------------
def kill(tick, rnd, attacker, victim, hs=False, assister=None, flash=False,
         weapon="ak47", aside=None, vside=None, aplace=None, vplace=None,
         vx=None, vy=None, ax=None, ay=None):
    """
    Build one kill event in the exact shape parse_demo.parse() emits.

    The positional arguments are all optional and are omitted from the dict
    when not given, because that is what the parser does - a demo with no
    coordinates for a world kill emits no ax/ay keys at all, and the missing-
    data paths in is_trade()/classify_death() are worth exercising as they
    actually occur.
    """
    ev = {
        "tick": tick,
        "round": rnd,
        "attacker": attacker,
        "victim": victim,
        "weapon": weapon,
        "hs": hs,
        "assister": assister,
        "flash_assist": flash,
    }
    for key, val in (("attacker_side", aside), ("victim_side", vside),
                     ("attacker_place", aplace), ("victim_place", vplace),
                     ("vx", vx), ("vy", vy), ("ax", ax), ("ay", ay)):
        if val is not None:
            ev[key] = val
    return ev


def fplayer(name, x=0.0, y=0.0, side="t", place=None, hp=100, alive=True):
    """One player inside a frame, in the parser's abbreviated key style."""
    p = {"n": name, "x": x, "y": y, "t": side, "hp": hp, "a": alive}
    if place is not None:
        p["pl"] = place
    return p


def frame(tick, *entries, rnd=None):
    f = {"tick": tick, "players": list(entries)}
    if rnd is not None:
        f["round"] = rnd
    return f


def make_match(kills, frames_=(), place_names=(), pov=None, round_nums=None,
               tickrate=64, bomb_plant=None):
    """A minimal match dict shaped like parse_demo's output, for analyze.py."""
    if round_nums is None:
        round_nums = sorted({k["round"] for k in kills if k.get("round") is not None}) or [1]
    rs = []
    for n in round_nums:
        r = {"round_num": n, "start": 0, "freeze_end": 0, "end": 10 ** 9,
             "official_end": 10 ** 9, "winner": "t", "reason": "ct_killed"}
        if bomb_plant is not None:
            r["bomb_plant"] = bomb_plant
        rs.append(r)
    names = sorted({k[s] for k in kills for s in ("attacker", "victim") if k.get(s)})
    return {
        "map": "de_anubis",
        "tickrate": tickrate,
        "sample_every": 32,
        "pov": pov,
        "players": players(*names),
        "place_names": list(place_names),
        "places": {},
        "rounds": rs,
        "kills": list(kills),
        "frames": list(frames_),
        "stats": {"rounds_played": len(rs)},
    }


def players(*names):
    return [{"name": n, "steamid": f"steam_{n}"} for n in names]


def rounds(*nums):
    return [{"round_num": n, "winner": "t", "reason": "ct_killed"} for n in nums]


# kcols is threaded through compute_stats but never actually read by it.
# Pinned here so a future signature change breaks loudly in one place.
KCOLS = {
    "tick": "tick", "attacker": "attacker_name", "victim": "victim_name",
    "weapon": "weapon", "hs": "headshot", "round": "round_num",
    "assist": "assister_name", "flash": "assistedflash",
    "ax": "attacker_X", "ay": "attacker_Y",
    "vx": "victim_X", "vy": "victim_Y",
}


@pytest.fixture
def kcols():
    return dict(KCOLS)


@pytest.fixture
def compute(stats_mod, kcols):
    """compute_stats with the boilerplate args pre-bound."""

    def _run(kills, player_names=None, round_nums=None, pov=None):
        if player_names is None:
            player_names = sorted(
                {k[side] for k in kills for side in ("attacker", "victim", "assister")
                 if k.get(side)}
            )
        if round_nums is None:
            round_nums = sorted({k["round"] for k in kills if k.get("round") is not None})
        return stats_mod.compute_stats(
            kills, rounds(*round_nums), players(*player_names), pov, kcols
        )

    return _run


# ---------------------------------------------------------------------------
# real data - rounds 1-5 of 2v5.dem (de_anubis, FACEIT)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def real_slice():
    """
    The ORIGINAL slice. Its kill records predate sides, coordinates and places,
    which makes it the regression net for the missing-data fallbacks - keep it
    that way and don't regenerate it.
    """
    path = FIXTURE_DIR / "kills_slice.json"
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def real_match():
    """
    The same five rounds re-cut with the CURRENT schema: sides, coordinates,
    callouts, and enough frames to resolve alive counts and spacing.
    """
    path = FIXTURE_DIR / "match_slice.json"
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def real_stats(stats_mod, real_slice, kcols):
    return stats_mod.compute_stats(
        real_slice["kills"], real_slice["rounds"], real_slice["players"], None, kcols
    )
