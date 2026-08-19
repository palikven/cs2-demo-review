"""
Tests for bullets and spray depth.

The failure that would be invisible: tracer direction. `angles_y` renders as a
perfectly plausible line whatever convention you assume, and a viewer full of
confident arrows pointing the wrong way looks exactly like one pointing the
right way. So it is checked against ground truth - every kill where the shooter
fired on that tick, the shot ray must point at where the victim actually fell.
On the bundled match that is 151 kills at a median error of 0.4 degrees.

The rest is the usual shape: bucket boundaries, a floor below which a hit rate
is noise, and the match table without which one player's 20% means nothing.
"""

import math
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import spray  # noqa: E402


def shot_match(shots):
    return {"utility": {"shots": shots}, "tickrate": 64}


def sh(by="a", i=0, hit=False, t=100, r=1, x=0.0, y=0.0, a=0.0):
    d = {"by": by, "i": i, "t": t, "r": r, "x": x, "y": y, "a": a}
    if hit:
        d["h"] = 1
    return d


class TestBuckets:
    @pytest.mark.parametrize("index,want", [
        (0, "1st"), (1, "2-4"), (3, "2-4"), (4, "5-9"),
        (8, "5-9"), (9, "10+"), (30, "10+"),
    ])
    def test_boundaries(self, index, want):
        assert spray.bucket_of(index) == want

    def test_the_first_bullet_is_its_own_bucket(self):
        """
        A tap and the first bullet of a spray are the same shot; lumping it in
        with 2-4 hides the one number every player already tracks.
        """
        assert spray.bucket_of(0) != spray.bucket_of(1)


class TestProfile:
    def test_hits_and_misses_are_counted_per_bucket(self):
        shots = ([sh(i=0, hit=True)] + [sh(i=0)] * 3
                 + [sh(i=10, hit=True)] + [sh(i=10)] * 19
                 + [sh(i=2)] * 20)
        p = spray.profile(shot_match(shots), "a")
        got = {b["bucket"]: (b["fired"], b["hit"]) for b in p["buckets"]}
        assert got["1st"] == (4, 1)
        assert got["10+"] == (20, 1)
        assert got["2-4"] == (20, 0)

    def test_a_player_who_barely_shot_is_not_rated(self):
        """Three bullets is not a hit rate, however it lands."""
        p = spray.profile(shot_match([sh(hit=True)] * 3), "a")
        assert p == {}

    def test_the_table_carries_the_other_players(self, real_match):
        """A 20% hit rate means nothing until you know the rest of the server."""
        shots = ((real_match.get("utility") or {}).get("shots"))
        if not shots:
            pytest.skip("fixture has no bullet data")
        t = spray.match_table(real_match)
        assert len(t) > 1
        assert all(0 <= v["rate"] <= 1 for v in t.values())

    def test_the_tail_is_reported_as_a_count(self, real_match):
        """
        Never as a verdict: spraying a smoke to deny a push scores zero here
        and is good play, and the demo cannot tell those apart.
        """
        shots = ((real_match.get("utility") or {}).get("shots"))
        if not shots:
            pytest.skip("fixture has no bullet data")
        p = spray.profile(real_match, real_match["pov"])
        if not p:
            pytest.skip("pov did not shoot enough in the fixture")
        assert isinstance(p["tail_fired"], int)
        assert "never as a verdict" in p["note"].lower()
        assert "anything to hit" in p["note"].lower()

    def test_shotgun_pellets_are_not_comparable_and_it_says_so(self, real_match):
        shots = ((real_match.get("utility") or {}).get("shots"))
        if not shots:
            pytest.skip("fixture has no bullet data")
        p = spray.profile(real_match, real_match["pov"])
        if not p:
            pytest.skip("pov did not shoot enough in the fixture")
        assert "shotgun" in p["note"].lower()


class TestTracerGeometry:
    """
    The one thing that renders convincingly while being completely wrong.
    """

    def test_shots_point_at_what_they_hit(self):
        import json
        path = ROOT / "data" / "2v5.json"
        if not path.exists():
            pytest.skip("no parsed match on this machine")
        m = json.loads(path.read_text(encoding="utf-8"))
        shots = (m.get("utility") or {}).get("shots")
        if not shots:
            pytest.skip("this parse has no bullet data")

        by_key = {}
        for s in shots:
            by_key.setdefault((s["by"], s["t"]), s)

        errors = []
        for k in m.get("kills") or []:
            if not k.get("attacker") or k.get("vx") is None:
                continue
            s = by_key.get((k["attacker"], k["tick"]))
            if not s:
                continue
            want = math.degrees(math.atan2(k["vy"] - s["y"], k["vx"] - s["x"]))
            errors.append(abs((s["a"] - want + 180) % 360 - 180))

        assert len(errors) > 30, "not enough kills matched a shot to prove much"
        median = statistics.median(errors)
        assert median < 3.0, (
            f"tracers do not point at what was shot (median {median:.1f} deg) - "
            f"the yaw convention is wrong")
        close = sum(1 for e in errors if e < 5)
        assert close / len(errors) > 0.9
