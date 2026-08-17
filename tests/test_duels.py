"""
Tests for the duel-approach model.

Two things here can produce a confident wrong answer rather than an obvious
failure, so most of these exist for them:

  * the round-boundary teleport. Consecutive frames across a round change are
    32 ticks apart like any other pair, but the players respawn between them.
    Measured naively that is 8100 u/s on the bundled demo - and it does not
    look like an error, it looks like a player who was sprinting into a duel.
  * the closure decomposition. "The gap shrank" is easy; "the victim is the one
    who shrank it" is the whole claim, and getting the sign or the reference
    point wrong swaps the blame between the two players silently.

The aggregate is tested for REFUSING to make a claim, which is the behaviour
that matters on a single match: the bundled demo shows the POV dying at 39 u/s
against a lobby median of 75, and that difference does not survive a
permutation test.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import duels  # noqa: E402
from conftest import fplayer, frame, kill, make_match  # noqa: E402


def line(name, ticks, xs, side="t", alive=True, rnd=None):
    """Frames of one player walking along the x axis."""
    return [frame(t, fplayer(name, x=x, side=side, alive=alive), rnd=rnd)
            for t, x in zip(ticks, xs)]


def merge(*frame_lists):
    """Combine per-player frame lists that share ticks."""
    out = {}
    for fl in frame_lists:
        for f in fl:
            slot = out.setdefault(f["tick"], {"tick": f["tick"], "players": []})
            slot["players"].extend(f["players"])
            if "round" in f:
                slot["round"] = f["round"]
    return [out[t] for t in sorted(out)]


def two_player_match(vx, ax, ticks=(0, 32, 64, 96), rounds_=None, kill_tick=100):
    """A victim and an attacker whose x positions are given per tick."""
    rounds_ = rounds_ or [1] * len(ticks)
    frames = merge(
        [frame(t, fplayer("V", x=x, side="t"), rnd=r)
         for t, x, r in zip(ticks, vx, rounds_)],
        [frame(t, fplayer("A", x=x, side="ct"), rnd=r)
         for t, x, r in zip(ticks, ax, rounds_)],
    )
    k = kill(kill_tick, 1, "A", "V")
    return make_match([k], frames, pov="V"), k


class TestSpeed:
    def test_it_measures_units_per_second(self):
        # 64 units in the last 32 ticks = 0.5s => 128 u/s
        m, k = two_player_match([0, 0, 100, 164], [500] * 4)
        fr = duels.Frames(m)
        assert duels.speed(fr, "V", 100) == pytest.approx(128.0)

    def test_a_standing_player_reads_zero_not_none(self):
        m, k = two_player_match([10] * 4, [500] * 4)
        assert duels.speed(duels.Frames(m), "V", 100) == 0.0

    def test_it_refuses_to_measure_across_a_round_change(self):
        """
        The one that matters. Same 32-tick spacing, but the player respawned -
        on the bundled demo this reads as 8100 u/s if you do not check.
        """
        m, k = two_player_match([0, 0, 50, 4000], [500] * 4,
                                rounds_=[1, 1, 1, 2])
        assert duels.speed(duels.Frames(m), "V", 100) is None

    def test_it_refuses_to_measure_across_a_demo_gap(self):
        m, k = two_player_match([0, 100, 200, 300], [500] * 4,
                                ticks=(0, 32, 64, 3000), kill_tick=3010)
        assert duels.speed(duels.Frames(m), "V", 3010) is None

    def test_a_dead_player_is_not_measured(self):
        frames = merge(
            [frame(t, fplayer("V", x=x, alive=(t < 64)), rnd=1)
             for t, x in zip((0, 32, 64, 96), (0, 10, 20, 30))],
            [frame(t, fplayer("A", x=500, side="ct"), rnd=1)
             for t in (0, 32, 64, 96)],
        )
        m = make_match([kill(100, 1, "A", "V")], frames, pov="V")
        assert duels.speed(duels.Frames(m), "V", 100) is None

    def test_it_uses_only_frames_strictly_before_the_kill(self):
        """
        A frame sampled exactly on the kill tick is the aftermath - the victim
        is dead in it and their coordinates are wherever the body landed.
        """
        m, k = two_player_match([0, 0, 0, 9999], [500] * 4,
                                ticks=(0, 32, 64, 96), kill_tick=96)
        fr = duels.Frames(m)
        assert fr.index_before(96) == 2, "the kill-tick frame was included"
        assert duels.speed(fr, "V", 96) == 0.0

    def test_an_unmeasurable_window_is_not_called_static(self):
        """None means 'not measured'. Reading it as 0 invents a stationary player."""
        assert duels.motion(None) is None
        assert duels.motion(0.0) == "static"


class TestClosure:
    """Who closed the distance - measured against the other's START position."""

    def test_the_victim_walking_in_is_attributed_to_the_victim(self):
        m, k = two_player_match([0, 100, 200, 300], [1000] * 4)
        a = duels.approach(duels.Frames(m), "V", "A", 100)
        assert a["closed_by"] == "victim"
        assert a["victim_closed"] == pytest.approx(300)
        assert a["attacker_closed"] == pytest.approx(0)

    def test_the_attacker_running_them_down_is_attributed_to_the_attacker(self):
        m, k = two_player_match([0] * 4, [1000, 900, 800, 700])
        a = duels.approach(duels.Frames(m), "V", "A", 100)
        assert a["closed_by"] == "attacker"
        assert a["attacker_closed"] == pytest.approx(300)
        assert a["victim_closed"] == pytest.approx(0)

    def test_both_walking_in_is_mutual(self):
        m, k = two_player_match([0, 100, 200, 300], [1200, 1100, 1000, 900])
        assert duels.approach(duels.Frames(m), "V", "A", 100)["closed_by"] == "mutual"

    def test_a_sightline_neither_crossed_is_neither(self):
        m, k = two_player_match([0, 5, 10, 8], [900, 902, 898, 900])
        assert duels.approach(duels.Frames(m), "V", "A", 100)["closed_by"] == "neither"

    def test_moving_away_is_negative_not_ignored(self):
        """
        A victim retreating and still dying is a different story from one
        holding still, and the sign is the only thing that carries it.
        """
        m, k = two_player_match([300, 200, 100, 0], [1000] * 4)
        a = duels.approach(duels.Frames(m), "V", "A", 100)
        assert a["victim_closed"] < 0
        assert a["closed_by"] == "neither"

    def test_a_step_across_a_long_sightline_is_not_a_push(self):
        """
        120 units is a real absolute distance and nothing at all across a
        4000-unit gap. Both floors have to hold for the claim to mean anything.
        """
        m, k = two_player_match([0, 40, 80, 120], [4000] * 4)
        a = duels.approach(duels.Frames(m), "V", "A", 100)
        assert a["victim_closed"] == pytest.approx(120)
        assert a["closed_by"] == "neither", "an absolute-only floor let this through"

    def test_the_same_step_across_a_short_gap_does_count(self):
        m, k = two_player_match([0, 60, 120, 180], [400] * 4)
        assert duels.approach(duels.Frames(m), "V", "A", 100)["closed_by"] == "victim"

    def test_a_shuffle_inside_a_close_gap_is_not_a_push(self):
        """
        The other floor, and the one the fraction test cannot reach. Closing 50
        of a 200-unit gap is 25% - clear of the relative bar - and is still two
        steps of strafing at knife range, not someone walking into a duel.
        """
        m, k = two_player_match([0, 20, 35, 50], [200] * 4)
        a = duels.approach(duels.Frames(m), "V", "A", 100)
        assert a["victim_closed"] == pytest.approx(50)
        assert a["victim_closed"] > duels.MIN_CLOSE_FRAC * a["gap_start"], \
            "test setup no longer exercises the absolute floor"
        assert a["closed_by"] == "neither", "a relative-only floor let this through"

    def test_it_refuses_across_a_round_change(self):
        m, k = two_player_match([0, 100, 200, 5000], [1000] * 4,
                                rounds_=[1, 1, 1, 2])
        assert duels.approach(duels.Frames(m), "V", "A", 100) is None

    def test_the_reported_lag_is_the_real_staleness(self):
        """
        The output has to carry how stale it is, because a coaching layer that
        reads 'static' as 'at the moment of death' is reading 0.5s of history
        as an instant.
        """
        m, k = two_player_match([0] * 4, [900] * 4, kill_tick=110)
        a = duels.approach(duels.Frames(m), "V", "A", 110)
        assert a["lag_s"] == pytest.approx((110 - 96) / 64, abs=0.01)


class TestSubjectPerspective:
    def test_a_death_reports_the_players_own_speed(self):
        m, k = two_player_match([0, 0, 100, 164], [500, 500, 500, 500])
        f = duels.duel(duels.Frames(m), k, subject="V")
        assert f["speed"] == pytest.approx(128)
        assert f["speed_other"] == 0.0

    def test_a_kill_flips_which_speed_is_whose(self):
        m, k = two_player_match([0, 0, 100, 164], [500, 500, 500, 500])
        f = duels.duel(duels.Frames(m), k, subject="A")
        assert f["speed"] == 0.0
        assert f["speed_other"] == pytest.approx(128)

    def test_closed_by_subject_tracks_the_subject_not_the_victim(self):
        m, k = two_player_match([0, 100, 200, 300], [1000] * 4)
        assert duels.duel(duels.Frames(m), k, subject="V")["closed_by_subject"] is True
        assert duels.duel(duels.Frames(m), k, subject="A")["closed_by_subject"] is False

    def test_a_suicide_is_not_a_duel(self):
        m = make_match([kill(100, 1, "V", "V")], [], pov="V")
        assert duels.duel(m, m["kills"][0]) is None


class TestPermutationTest:
    def test_identical_distributions_do_not_look_significant(self):
        a = [10, 20, 30, 40, 50, 60, 70, 80]
        assert duels.permutation_p(a, list(a), n=2000) > 0.5

    def test_a_real_separation_is_detected(self):
        a = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] * 2
        b = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109] * 2
        assert duels.permutation_p(a, b, n=2000) < 0.01

    def test_it_never_returns_exactly_zero(self):
        """
        The observed arrangement is one of the permutations. A test that can
        report p=0 is claiming certainty it cannot have.

        The groups must not be two constants: with every value identical inside
        a group, an unbalanced shuffle reproduces the whole median gap, hits is
        never 0, and the test passes with the +1 deleted.
        """
        a = list(range(1, 21))
        b = list(range(1000, 1020))
        assert duels.permutation_p(a, b, n=500) > 0

    def test_too_small_a_sample_is_none_not_a_p_value(self):
        assert duels.permutation_p([1], [2, 3, 4], n=100) is None
        assert duels.permutation_p([], [], n=100) is None

    def test_the_same_data_gives_the_same_answer_twice(self):
        """A p-value that moves between runs is not something anyone can act on."""
        a, b = [1, 5, 3, 9, 2, 8], [4, 6, 2, 7, 3, 5]
        assert duels.permutation_p(a, b, n=1000) == duels.permutation_p(a, b, n=1000)


class TestProfileRefusesWeakClaims:
    def test_one_match_of_deaths_is_not_a_movement_habit(self, real_match):
        """
        The POV dies at 39 u/s against a lobby median of 75, which looks like a
        finding until it is tested. If this ever starts passing as claimable on
        a single match, the threshold has been loosened, not the evidence
        improved.
        """
        p = duels.profile(real_match, real_match["pov"])
        assert p["deaths_measured"] > 0, "fixture has no measurable deaths"
        assert p["claimable"] is False
        assert "not enough evidence" in p["note"].lower()

    def test_a_clear_separation_with_enough_duels_is_claimable(self):
        """
        The refusal has to be about the evidence, not a hard-coded False. This
        builds a player who genuinely dies sprinting while everyone else dies
        planted, with enough duels to resolve it.

        The two groups need real spread, not one constant each. With every
        value inside a group identical, any unbalanced shuffle reproduces the
        entire median gap and the test reports p=0.75 on a separation of two
        orders of magnitude - a property of degenerate input, not of the code.
        """
        frames, kills = [], []
        for i in range(20):
            base = i * 10000                     # each duel in its own tick range
            fast = 80 + 7 * (i % 11)             # ~80-150 units per sample
            slow = 1 + 3 * (i % 7)               # ~1-19
            for step, t in enumerate((base, base + 32, base + 64)):
                frames.append(frame(t, fplayer("ME", x=step * fast),
                                    fplayer("OTHER", x=1000 + step * slow),
                                    fplayer("K", x=5000, side="ct"), rnd=1))
            kills.append(kill(base + 70, 1, "K", "ME"))
            kills.append(kill(base + 71, 1, "K", "OTHER"))
        m = make_match(kills, frames, pov="ME")
        p = duels.profile(m, "ME")
        assert p["deaths_measured"] >= duels.MIN_DUELS_FOR_CLAIM
        assert p["p_vs_lobby"] < duels.P_THRESHOLD
        assert p["claimable"] is True
        assert "significant" in p["note"].lower()

    def test_a_significant_p_on_a_handful_of_duels_is_still_refused(self):
        """
        The count floor, independent of the p-value. Eight deaths against eight
        cleanly separated lobby deaths clears p<0.05 comfortably, and eight
        duels is still not a habit. Without the floor, one unlucky half of one
        match becomes a diagnosis.

        Eight rather than five: a 5-vs-5 split has only 252 arrangements, and
        enough of them reach a clean separation that this setup lands at
        p=0.0509 - the right side of nothing, and too close to the threshold to
        be a stable test of the floor.
        """
        frames, kills = [], []
        for i in range(8):
            base = i * 10000
            fast, slow = 90 + 6 * i, 1 + 2 * i
            for step, t in enumerate((base, base + 32, base + 64)):
                frames.append(frame(t, fplayer("ME", x=step * fast),
                                    fplayer("OTHER", x=1000 + step * slow),
                                    fplayer("K", x=5000, side="ct"), rnd=1))
            kills.append(kill(base + 70, 1, "K", "ME"))
            kills.append(kill(base + 71, 1, "K", "OTHER"))
        p = duels.profile(make_match(kills, frames, pov="ME"), "ME")
        assert p["deaths_measured"] == 8
        assert p["p_vs_lobby"] < duels.P_THRESHOLD, \
            "test setup no longer exercises the count floor"
        assert p["claimable"] is False

    def test_the_baseline_excludes_the_player_themselves(self, real_match):
        """
        Comparing a player against a lobby that includes them drags the
        baseline toward them and shrinks every difference.
        """
        p = duels.profile(real_match, real_match["pov"])
        total = sum(1 for k in real_match["kills"]
                    if k.get("attacker") and k["attacker"] != k.get("victim"))
        assert p["deaths_measured"] + p["lobby_deaths_measured"] <= total

    def test_a_player_who_is_not_in_the_match_yields_nothing(self, real_match):
        p = duels.profile(real_match, "nobody-by-that-name")
        assert p["deaths_measured"] == 0
        assert p["claimable"] is False


class TestAnnotate:
    def test_it_attaches_the_players_own_motion_to_deaths_and_kills(self):
        """
        The victim's speed on a death and the attacker's on a kill. Attaching
        the wrong side reads plausibly and blames the wrong player.
        """
        frames = merge(
            [frame(t, fplayer("ME", x=x), rnd=1)
             for t, x in zip((0, 32, 64, 96), (0, 0, 0, 0))],
            [frame(t, fplayer("E", x=x, side="ct"), rnd=1)
             for t, x in zip((0, 32, 64, 96), (900, 900, 900, 1028))],
        )
        deaths = [{"tick": 100}]
        kills_ = [{"tick": 100}]
        m = make_match([kill(100, 1, "E", "ME"), kill(100, 1, "ME", "E")],
                       frames, pov="ME")
        duels.annotate(m, deaths, kills_, "ME")
        assert deaths[0]["motion"] == "static"
        assert deaths[0]["motion_other"] == "running"
        assert kills_[0]["motion"] == "static"

    def test_it_leaves_unmeasurable_records_untouched(self):
        m = make_match([kill(100, 1, "E", "ME")], [], pov="ME")
        deaths = [{"tick": 100}]
        duels.annotate(m, deaths, [], "ME")
        assert deaths == [{"tick": 100}], "an unmeasurable duel invented fields"
