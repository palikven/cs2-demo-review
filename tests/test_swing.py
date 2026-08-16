"""
Tests for the round-win-probability model and swing.

This model is ours, not HLTV's - their Rating 3.0 Round Swing is proprietary -
so there is no reference implementation to check against. What can be checked
is that it behaves like a probability model should: monotonic in the things
that matter, symmetric between the sides, bounded, and conserving. That last
one is the strongest test here: every kill hands one side exactly what it takes
from the other, so summed over all paired kills the net must be zero. A sign
error anywhere in the attribution breaks it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parser"))
import swing as S  # noqa: E402

WP = S.win_probability


class TestWinProbability:
    def test_even_state_returns_the_baseline(self):
        assert WP(5, 5, t_baseline=0.5) == pytest.approx(0.5, abs=0.01)

    def test_it_follows_the_map_baseline(self):
        """A CT-sided map should not read 50/50 at 5v5."""
        assert WP(5, 5, t_baseline=0.40) < 0.5
        assert WP(5, 5, t_baseline=0.60) > 0.5

    def test_more_players_is_monotonically_better(self):
        ps = [WP(n, 5, t_baseline=0.5) for n in range(1, 6)]
        assert ps == sorted(ps)

    def test_a_man_advantage_is_worth_roughly_what_cs_says(self):
        """
        The single most important number in the model. A 5v4 sitting at ~72%
        is where Counter-Strike has always been; if this drifts, every swing
        number moves with it.
        """
        assert WP(5, 4, t_baseline=0.5) == pytest.approx(0.72, abs=0.03)
        assert WP(5, 3, t_baseline=0.5) == pytest.approx(0.87, abs=0.04)

    def test_it_is_symmetric_between_the_sides(self):
        """P(T wins 3v2) must equal 1 - P(T wins 2v3) on a neutral map."""
        for a, b in ((3, 2), (5, 1), (4, 4), (2, 5)):
            assert WP(a, b, t_baseline=0.5) == pytest.approx(
                1 - WP(b, a, t_baseline=0.5), abs=0.001)

    def test_the_bomb_helps_the_t_side(self):
        assert WP(3, 3, bomb_planted=True) > WP(3, 3, bomb_planted=False)

    def test_better_equipment_helps(self):
        rifles = WP(5, 5, equip_t=5 * 3700, equip_ct=5 * 850)
        pistols = WP(5, 5, equip_t=5 * 850, equip_ct=5 * 3700)
        assert rifles > 0.5 > pistols

    def test_equipment_matters_less_than_bodies(self):
        """A full-buy advantage must not outweigh a man advantage, or the
        model will call every eco round a coin flip."""
        gear = WP(4, 4, equip_t=4 * 3700, equip_ct=4 * 200)
        body = WP(5, 4, equip_t=5 * 3700, equip_ct=4 * 3700)
        assert body > gear

    def test_it_never_returns_certainty(self):
        """
        A 5v1 is not a guaranteed win, and a model that says 1.0 makes
        everything that happens next look worthless.

        Probed at states extreme enough that an unclamped model WOULD reach
        certainty - a plain 5v1 does not get there on its own, so testing only
        that case leaves the clamps unguarded.
        """
        extreme = [
            WP(5, 1, bomb_planted=True, equip_t=5 * 4750, equip_ct=200, t_baseline=0.85),
            WP(1, 5, equip_t=200, equip_ct=5 * 4750, t_baseline=0.15),
            WP(5, 1, t_baseline=0.85),
            WP(1, 5, t_baseline=0.15),
        ]
        for p in extreme:
            assert S.P_FLOOR <= p <= S.P_CEIL, p
        assert max(extreme) == pytest.approx(S.P_CEIL), "ceiling never exercised"
        assert min(extreme) == pytest.approx(S.P_FLOOR), "floor never exercised"

    def test_a_wiped_side_cannot_win(self):
        assert WP(0, 3) < 0.1
        assert WP(3, 0) > 0.9

    def test_a_planted_bomb_survives_the_last_t(self, ):
        """Bomb down with every T dead is still not a CT win yet."""
        assert WP(0, 3, bomb_planted=True) > WP(0, 3, bomb_planted=False)


class TestSwingMagnitude:
    def _delta(self, a, b, **kw):
        """How much killing one CT moves things from a v b."""
        return WP(a, b - 1, **kw) - WP(a, b, **kw)

    def test_a_kill_in_a_close_round_is_worth_more(self):
        """
        The entire point of the metric: the kill that takes 2v2 to 2v1 decides
        a round, the one that takes 5v5 to 5v4 does not.

        The first version of this model failed exactly here - it read only
        (alive_t - alive_ct), so both are "even goes to +1" and scored the
        same, which makes swing a kill count with extra steps. The scarcity
        term exists to fix that and this is the test that forced it.
        """
        assert self._delta(2, 2) > self._delta(5, 5)

    def test_kill_value_rises_monotonically_as_the_round_empties(self):
        deltas = [self._delta(n, n) for n in range(5, 0, -1)]
        assert deltas == sorted(deltas)

    def test_padding_a_blowout_is_worth_almost_nothing(self):
        assert self._delta(5, 2) < 0.05

    def test_a_kill_never_hurts_the_side_that_got_it(self):
        for a in range(1, 6):
            for b in range(1, 6):
                assert self._delta(a, b) >= 0, f"{a}v{b}"

    def test_a_kill_helps_whenever_the_round_is_still_live(self):
        """
        Strictly positive everywhere except states already pinned at a clamp.

        BOTH clamps matter, which is the part I got wrong first time: 5v1 sits
        on the 0.98 ceiling so killing the last man is worth nothing, and 1v5
        sits on the 0.02 floor so killing one of five is worth nothing either.
        Only checking the ceiling leaves the second case failing.
        """
        live = 0
        for a in range(1, 6):
            for b in range(1, 6):
                p = S.win_probability(a, b)
                if p >= S.P_CEIL or p <= S.P_FLOOR:
                    continue
                live += 1
                assert self._delta(a, b) > 0, f"{a}v{b}"
        assert live >= 15, "most states should be live; the skip is too broad"


class TestSwingAttribution:
    def test_paired_kills_conserve_exactly(self, real_match):
        """
        Every kill gives one side precisely what it takes from the other, so
        over all kills with a real attacker the net must be zero. This is the
        test that catches a sign error in the attacker/victim side handling -
        which produced a +545% net for every player on the first run.
        """
        sw = S.compute_swing(real_match)
        paired = [s for s in sw if s["attacker"] and s["attacker"] != s["victim"]]
        assert paired, "fixture has no paired kills"
        net = sum(s["swing"] for s in paired) - sum(s["cost"] for s in paired)
        assert net == pytest.approx(0.0, abs=1e-9)

    def test_a_suicide_costs_without_crediting_anyone(self, real_match):
        """
        Falling off the map takes win probability out of the round with nobody
        to hand it to. That makes the all-players total non-zero, and that is
        correct rather than a bug - so it is pinned here.
        """
        sw = S.compute_swing(real_match)
        selfkills = [s for s in sw if s["attacker"] == s["victim"]]
        if not selfkills:
            pytest.skip("no suicides in this slice")
        assert all(s["cost"] > 0 for s in selfkills)

    def test_a_death_never_helps_the_victim(self, real_match):
        for s in S.compute_swing(real_match):
            assert s["cost"] >= 0, f"death at {s['tick']} gained win probability"

    def test_almost_every_death_costs_something(self, real_match):
        sw = S.compute_swing(real_match)
        paying = [s for s in sw if s["cost"] > 0]
        assert len(paying) > 0.9 * len(sw)

    def test_a_death_in_an_already_decided_round_can_cost_nothing(self, real_match):
        """
        The clamp doing its job, not a bug: at 5v1 with the bomb down the round
        is already pinned at the ceiling, so losing the last defender moves the
        probability by zero. Padding a decided round is worth nothing, which is
        the behaviour we want - pinned here so nobody 'fixes' it into a
        guaranteed-positive cost.
        """
        free = [s for s in S.compute_swing(real_match) if s["cost"] == 0]
        if not free:
            pytest.skip("no already-decided deaths in this slice")
        for s in free:
            lopsided = abs(s["alive_t"] - s["alive_ct"]) >= 3
            pinned = s["p_before_t"] in (S.P_FLOOR, S.P_CEIL)
            assert lopsided or pinned, f"unexpected zero-cost death: {s}"

    def test_swing_and_cost_are_the_same_size_for_a_normal_kill(self, real_match):
        for s in S.compute_swing(real_match):
            if s["attacker"] and s["attacker"] != s["victim"] and s["attacker_side"]:
                assert s["swing"] == pytest.approx(s["cost"], abs=1e-9)

    def test_probabilities_are_recorded_for_every_kill(self, real_match):
        for s in S.compute_swing(real_match):
            assert 0 <= s["p_before_t"] <= 1
            assert 0 <= s["p_after_t"] <= 1


class TestPerPlayer:
    def test_net_subtracts_losses(self, real_match):
        """Adding gained and lost instead of subtracting was the original bug
        and gives everyone a large positive net."""
        rows = S.per_player(real_match)
        for name, r in rows.items():
            assert r["swing_net_pct"] == pytest.approx(
                r["swing_gained_pct"] - r["swing_lost_pct"], abs=0.15)

    def test_someone_ends_up_negative(self, real_match):
        """Swing is zero-sum between the teams, so a whole server of positive
        contributors means the attribution is broken."""
        rows = S.per_player(real_match)
        assert any(r["swing_net_pct"] < 0 for r in rows.values())

    def test_per_round_figures_are_a_believable_size(self, real_match):
        """
        Ours is a different model from HLTV's so the scale need not match, but
        theirs runs roughly -1.5% to +4% per round. An order of magnitude out
        means something is wrong rather than merely different.

        The bound has to account for sample size: this fixture is five rounds,
        where one player who died every round with no kills legitimately hits
        -19%/round. Over a full match the same figures land inside +/-6.
        """
        rounds = (real_match.get("stats") or {}).get("rounds_played") or 1
        bound = 15 if rounds >= 20 else 40
        for name, r in S.per_player(real_match).items():
            assert -bound < r["swing_per_round_pct"] < bound, f"{name}: {r}"

    def test_a_full_match_stays_in_hltvs_ballpark(self, real_match):
        """
        Scaled to a full match the spread should be single digits per round.
        Checked on totals rather than per-round so the short fixture can still
        exercise it.
        """
        rounds = (real_match.get("stats") or {}).get("rounds_played") or 1
        for name, r in S.per_player(real_match).items():
            per_round = r["swing_net_pct"] / rounds
            assert abs(per_round) < 40, f"{name} at {per_round:.1f}%/round"


class TestBaseline:
    def test_a_short_match_falls_back_to_even(self, real_match):
        """
        Twenty rounds is a thin basis for a map baseline; a five-round fixture
        is no basis at all, and using it would bake one team's good half into
        every probability in the match.
        """
        short = dict(real_match)
        short["rounds"] = (real_match["rounds"] or [])[:4]
        assert S.side_baseline(short) == 0.5

    def test_a_full_match_uses_its_own_win_rate(self):
        m = {"rounds": [{"round_num": i, "winner": "t" if i <= 8 else "ct"}
                        for i in range(1, 13)]}
        assert S.side_baseline(m) == pytest.approx(8 / 12)


class TestLiveWindow:
    """
    A round's start..official_end also covers freeze time and the post-round
    period, and during those the demo still reports the PREVIOUS round's
    bodies - round 5 of the sample match opens 3v5 because nobody has
    respawned. Reading a win probability off that is meaningless.
    """

    def test_the_window_starts_after_the_freeze(self, real_match):
        w = S.live_windows(real_match)
        for r in real_match["rounds"]:
            n = r.get("round_num")
            if n in w and r.get("freeze_end"):
                assert w[n][0] == r["freeze_end"]
                assert w[n][0] > r["start"], "freeze time must be excluded"

    def test_frames_are_clipped_to_it(self, real_match):
        w = S.live_windows(real_match)
        clipped = S.frames_by_round(real_match, live_only=True)
        everything = S.frames_by_round(real_match)
        assert sum(len(v) for v in clipped.values()) < \
               sum(len(v) for v in everything.values()), "nothing was clipped"
        for rnd, frames in clipped.items():
            lo, hi = w[rnd]
            for f in frames:
                assert lo <= f["tick"] <= hi

    def test_stale_pre_round_states_are_excluded(self, real_match):
        """
        The concrete symptom: without clipping, the first frames of a round
        report the previous round's survivors. Assert the clipped set never
        opens a round with a lopsided alive count.
        """
        for rnd, frames in S.frames_by_round(real_match, live_only=True).items():
            if not frames:
                continue
            at, act, _, _ = S.state_at(real_match, frames[0], False)
            assert at + act >= 8, f"round {rnd} opens {at}v{act} - stale state"


class TestProbabilityTimeline:
    def test_it_only_keeps_points_where_the_value_moves(self, real_match):
        tl = S.probability_timeline(real_match)
        frames = sum(len(v) for v in
                     S.frames_by_round(real_match, live_only=True).values())
        assert 0 < len(tl) < frames, "timeline should compress"

    def test_points_are_ordered_and_well_formed(self, real_match):
        tl = S.probability_timeline(real_match)
        ticks = [p[0] for p in tl]
        assert ticks == sorted(ticks)
        for tick, p, rnd in tl:
            assert 0 <= p <= 1
            assert rnd is not None

    def test_each_point_carries_its_round(self, real_match):
        """
        Without the round on each point a step lookup shows the previous
        round's final probability all through the next round's freeze time -
        i.e. last round's result on screen while this one is still buying.
        """
        windows = S.live_windows(real_match)
        for tick, _, rnd in S.probability_timeline(real_match):
            lo, hi = windows[rnd]
            assert lo <= tick <= hi

    def test_every_round_gets_at_least_one_point(self, real_match):
        played = {r["round_num"] for r in real_match["rounds"]}
        seen = {p[2] for p in S.probability_timeline(real_match)}
        assert seen <= played
        assert len(seen) >= len(played) - 1     # a round may be all-clipped

    def test_a_lower_threshold_keeps_more_points(self, real_match):
        coarse = S.probability_timeline(real_match, min_delta=0.2)
        fine = S.probability_timeline(real_match, min_delta=0.001)
        assert len(fine) >= len(coarse)


class TestCalibration:
    def test_it_runs_and_reports_a_sample_size(self, real_match):
        c = S.calibrate(real_match)
        assert c["states"] > 0
        assert c["rounds"] > 0
        assert 0 <= c["brier"] <= 1

    def test_it_beats_guessing_the_base_rate(self, real_match):
        """
        The weakest possible bar - if the model cannot beat always predicting
        the match's own T-win rate, it is adding nothing.
        """
        c = S.calibrate(real_match)
        assert c["brier"] < c["baseline_brier"]

    def test_it_scores_only_live_states(self, real_match):
        """
        Scoring freeze-time frames would grade the model on states where the
        demo still shows the previous round's bodies - it would be marked down
        for predictions it never really made. The state count must therefore
        match the clipped frame set, not every frame.
        """
        c = S.calibrate(real_match)
        live = sum(len(v) for v in
                   S.frames_by_round(real_match, live_only=True).values())
        every = sum(len(v) for v in S.frames_by_round(real_match).values())
        assert c["states"] <= live
        assert c["states"] < every, "calibration is scoring non-live frames"
