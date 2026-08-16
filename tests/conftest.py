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


# ---------------------------------------------------------------------------
# synthetic builders - keep tests readable and intention-revealing
# ---------------------------------------------------------------------------
def kill(tick, rnd, attacker, victim, hs=False, assister=None, flash=False,
         weapon="ak47"):
    """Build one kill event in the exact shape parse_demo.parse() emits."""
    return {
        "tick": tick,
        "round": rnd,
        "attacker": attacker,
        "victim": victim,
        "weapon": weapon,
        "hs": hs,
        "assister": assister,
        "flash_assist": flash,
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
    path = FIXTURE_DIR / "kills_slice.json"
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def real_stats(stats_mod, real_slice, kcols):
    return stats_mod.compute_stats(
        real_slice["kills"], real_slice["rounds"], real_slice["players"], None, kcols
    )
