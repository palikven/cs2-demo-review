"""
Tests for parser/analyze.py - the positional layer.

The stakes here are different from test_stats.py. Those tests protect numbers a
player can check against a scoreboard; these protect claims the coaching layer
will state as fact ("you died in Palace four times, untraded"). A wrong number
there isn't a cosmetic bug, it's the AI confidently telling someone to fix a
habit they don't have - which is exactly the failure mode the whole
compute-it-in-Python design exists to prevent.

Conventions match test_stats.py: synthetic builders from conftest for the
edge cases, and the real 5-round slice for the "does this hold up on an actual
demo" pass.
"""

import json

import pytest

from conftest import fplayer, frame, kill, make_match


# ---------------------------------------------------------------------------
class TestClassifyDeath:
    def test_carries_both_callouts(self, analyze_mod):
        m = make_match([kill(100, 1, "enemy", "me", aside="ct", vside="t",
                             aplace="Connector", vplace="Palace")], pov="me")
        d = analyze_mod.analyze(m)["deaths"][0]
        assert d["victim_place"] == "Palace"
        assert d["attacker_place"] == "Connector"

    def test_world_death_is_not_classifiable(self, analyze_mod):
        """Falling off the map is not a duel anybody can be coached about."""
        m = make_match([kill(100, 1, None, "me", vplace="Canal")], pov="me")
        assert analyze_mod.analyze(m)["deaths"] == []

    def test_suicide_is_not_classifiable(self, analyze_mod):
        m = make_match([kill(100, 1, "me", "me", vplace="Canal")], pov="me")
        assert analyze_mod.analyze(m)["deaths"] == []

    def test_seconds_measured_from_freeze_end_not_round_start(self, analyze_mod):
        """Buy time isn't round time - a 5s death means 5s after the gates."""
        m = make_match([kill(64 * 12, 1, "enemy", "me", vside="t")], pov="me")
        m["rounds"][0]["start"] = 0
        m["rounds"][0]["freeze_end"] = 64 * 2
        assert analyze_mod.analyze(m)["deaths"][0]["t"] == 10.0

    def test_post_plant_flag(self, analyze_mod):
        m = make_match([kill(500, 1, "enemy", "me", vside="t")], pov="me",
                       bomb_plant=400)
        assert analyze_mod.analyze(m)["deaths"][0]["post_plant"] is True

    def test_not_post_plant_before_the_plant(self, analyze_mod):
        m = make_match([kill(300, 1, "enemy", "me", vside="t")], pov="me",
                       bomb_plant=400)
        assert analyze_mod.analyze(m)["deaths"][0]["post_plant"] is False

    def test_awp_is_flagged_separately_from_weapon(self, analyze_mod):
        m = make_match([kill(100, 1, "enemy", "me", weapon="awp", vside="t")], pov="me")
        d = analyze_mod.analyze(m)["deaths"][0]
        assert d["awp"] is True and d["weapon"] == "awp"

    def test_rifle_is_not_an_awp(self, analyze_mod):
        m = make_match([kill(100, 1, "enemy", "me", weapon="ak47", vside="t")], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["awp"] is False


# ---------------------------------------------------------------------------
class TestAliveCounts:
    def _match(self, tick=200):
        # 2 alive on t (me + mate), 3 on ct
        fr = frame(192,
                   fplayer("me", 0, 0, "t"), fplayer("mate", 100, 0, "t"),
                   fplayer("dead_mate", 0, 0, "t", hp=0, alive=False),
                   fplayer("e1", 500, 0, "ct"), fplayer("e2", 600, 0, "ct"),
                   fplayer("e3", 700, 0, "ct"))
        return make_match([kill(tick, 1, "e1", "me", aside="ct", vside="t",
                                vx=0.0, vy=0.0)], [fr], pov="me")

    def test_counts_come_from_the_frame_before_the_kill(self, analyze_mod):
        d = analyze_mod.analyze(self._match())["deaths"][0]
        assert (d["alive_own"], d["alive_enemy"]) == (2, 3)

    def test_dead_players_are_not_counted_alive(self, analyze_mod):
        d = analyze_mod.analyze(self._match())["deaths"][0]
        assert d["alive_own"] == 2      # not 3 - dead_mate is excluded

    def test_alive_flag_alone_marks_a_player_dead(self, analyze_mod):
        """
        a:false and hp:0 are independent signals and each has to stand on its
        own. The parser writes both, but only because this demo happened to
        carry is_alive - drop that prop and hp is all there is, and a corpse
        whose hp never got zeroed would otherwise pad the alive count and
        silently turn a 4v5 into a 5v5 in the coaching output.
        """
        fr = frame(192, fplayer("me", 0, 0, "t"),
                   fplayer("corpse", 10, 0, "t", hp=100, alive=False),
                   fplayer("e1", 500, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t")], [fr], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["alive_own"] == 1

    def test_zero_hp_alone_marks_a_player_dead(self, analyze_mod):
        fr = frame(192, fplayer("me", 0, 0, "t"),
                   fplayer("corpse", 10, 0, "t", hp=0, alive=True),
                   fplayer("e1", 500, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t")], [fr], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["alive_own"] == 1

    def test_man_state_down(self, analyze_mod):
        assert analyze_mod.analyze(self._match())["deaths"][0]["man_state"] == "down"

    def test_man_state_even(self, analyze_mod):
        fr = frame(192, fplayer("me", 0, 0, "t"), fplayer("e1", 500, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t")], [fr], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["man_state"] == "even"

    def test_man_state_up(self, analyze_mod):
        fr = frame(192, fplayer("me", 0, 0, "t"), fplayer("mate", 1, 0, "t"),
                   fplayer("e1", 500, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t")], [fr], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["man_state"] == "up"

    def test_uses_last_frame_at_or_before_the_tick(self, analyze_mod):
        """A frame sampled AFTER the death must not be used - by then I'm dead."""
        before = frame(192, fplayer("me", 0, 0, "t"), fplayer("mate", 1, 0, "t"),
                       fplayer("e1", 500, 0, "ct"))
        after = frame(224, fplayer("me", 0, 0, "t", hp=0, alive=False),
                      fplayer("mate", 1, 0, "t"), fplayer("e1", 500, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t")],
                       [before, after], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["alive_own"] == 2


# ---------------------------------------------------------------------------
class TestIsolation:
    def test_measures_nearest_living_teammate(self, analyze_mod):
        fr = frame(192, fplayer("me", 0, 0, "t"), fplayer("near", 300, 0, "t"),
                   fplayer("far", 2000, 0, "t"), fplayer("e1", 50, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t",
                             vx=0.0, vy=0.0)], [fr], pov="me")
        d = analyze_mod.analyze(m)["deaths"][0]
        assert d["nearest_mate"] == 300 and d["nearest_mate_name"] == "near"

    def test_enemies_are_never_counted_as_support(self, analyze_mod):
        """An enemy standing on top of you is the opposite of support."""
        fr = frame(192, fplayer("me", 0, 0, "t"), fplayer("mate", 2000, 0, "t"),
                   fplayer("e1", 10, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t",
                             vx=0.0, vy=0.0)], [fr], pov="me")
        d = analyze_mod.analyze(m)["deaths"][0]
        assert d["nearest_mate"] == 2000 and d["isolated"] is True

    def test_dead_teammates_do_not_count_as_support(self, analyze_mod):
        fr = frame(192, fplayer("me", 0, 0, "t"),
                   fplayer("corpse", 10, 0, "t", hp=0, alive=False),
                   fplayer("alive_mate", 1500, 0, "t"), fplayer("e1", 50, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t",
                             vx=0.0, vy=0.0)], [fr], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["nearest_mate"] == 1500

    def test_last_man_alive_has_no_distance_at_all(self, analyze_mod):
        """No teammates left is absence of data, not a distance of zero."""
        fr = frame(192, fplayer("me", 0, 0, "t"), fplayer("e1", 50, 0, "ct"))
        m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t",
                             vx=0.0, vy=0.0)], [fr], pov="me")
        d = analyze_mod.analyze(m)["deaths"][0]
        assert "nearest_mate" not in d and "isolated" not in d

    def test_isolated_threshold_is_exclusive(self, analyze_mod):
        thresh = analyze_mod.ISOLATED_UNITS
        for dist, expected in ((thresh - 1, False), (thresh, False), (thresh + 1, True)):
            fr = frame(192, fplayer("me", 0, 0, "t"), fplayer("mate", dist, 0, "t"),
                       fplayer("e1", 5, 0, "ct"))
            m = make_match([kill(200, 1, "e1", "me", aside="ct", vside="t",
                                 vx=0.0, vy=0.0)], [fr], pov="me")
            assert analyze_mod.analyze(m)["deaths"][0]["isolated"] is expected

    def test_distance_ignores_height(self, analyze_mod):
        """2D on purpose - a teammate above you on Nuke is not supporting you."""
        assert analyze_mod.dist2d(0, 0, 300, 0) == 300


# ---------------------------------------------------------------------------
class TestRepeek:
    def _pair(self, gap_ticks, same_place=True, same_side=True):
        mate_place = "Palace" if same_place else "Canal"
        return make_match([
            kill(1000, 1, "e1", "mate", aside="ct",
                 vside="t" if same_side else "ct", vplace=mate_place),
            kill(1000 + gap_ticks, 1, "e1", "me", aside="ct", vside="t",
                 vplace="Palace"),
        ], pov="me")

    def test_flags_walking_into_a_duel_a_teammate_just_lost(self, analyze_mod):
        d = analyze_mod.analyze(self._pair(64 * 3))["deaths"][0]
        assert d["repeek"] is True and d["repeek_after"] == 3.0

    def test_not_flagged_outside_the_window(self, analyze_mod):
        gap = int(analyze_mod.REPEEK_SECONDS * 64) + 64
        assert analyze_mod.analyze(self._pair(gap))["deaths"][0]["repeek"] is False

    def test_boundary_is_inclusive(self, analyze_mod):
        gap = int(analyze_mod.REPEEK_SECONDS * 64)
        assert analyze_mod.analyze(self._pair(gap))["deaths"][0]["repeek"] is True

    def test_not_flagged_for_a_different_callout(self, analyze_mod):
        d = analyze_mod.analyze(self._pair(64 * 3, same_place=False))["deaths"][0]
        assert d["repeek"] is False

    def test_an_enemy_dying_there_is_not_a_repeek(self, analyze_mod):
        """Them losing a man in Palace is a reason to go, not a warning."""
        d = analyze_mod.analyze(self._pair(64 * 3, same_side=False))["deaths"][0]
        assert d["repeek"] is False

    def test_does_not_look_across_rounds(self, analyze_mod):
        m = make_match([
            kill(1000, 1, "e1", "mate", aside="ct", vside="t", vplace="Palace"),
            kill(1064, 2, "e1", "me", aside="ct", vside="t", vplace="Palace"),
        ], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["repeek"] is False


# ---------------------------------------------------------------------------
class TestOpeningDuel:
    def test_first_player_kill_of_the_round_is_the_opener(self, analyze_mod):
        m = make_match([
            kill(100, 1, "e1", "me", aside="ct", vside="t"),
            kill(200, 1, "e1", "mate", aside="ct", vside="t"),
        ], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["opening"] is True

    def test_second_death_is_not_an_opener(self, analyze_mod):
        m = make_match([
            kill(100, 1, "e1", "mate", aside="ct", vside="t"),
            kill(200, 1, "e1", "me", aside="ct", vside="t"),
        ], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["opening"] is False

    def test_a_world_death_does_not_consume_the_opening_duel(self, analyze_mod):
        """Someone falling off the map hasn't opened anything."""
        m = make_match([
            kill(50, 1, None, "mate", vside="t"),
            kill(100, 1, "e1", "me", aside="ct", vside="t"),
        ], pov="me")
        assert analyze_mod.analyze(m)["deaths"][0]["opening"] is True


# ---------------------------------------------------------------------------
class TestTradeSharedWithStats:
    """is_trade() moved out of compute_stats; both callers must still agree."""

    def test_teammate_avenging_inside_the_window_is_a_trade(self, stats_mod):
        k = kill(1000, 1, "e1", "me", aside="ct", vside="t")
        later = [kill(1100, 1, "mate", "e1", aside="t", vside="ct")]
        assert stats_mod.is_trade(k, later) is True

    def test_boundary_tick_counts(self, stats_mod):
        w = stats_mod.TRADE_WINDOW_TICKS
        k = kill(1000, 1, "e1", "me", aside="ct", vside="t")
        assert stats_mod.is_trade(k, [kill(1000 + w, 1, "mate", "e1",
                                           aside="t", vside="ct")]) is True

    def test_one_tick_past_the_boundary_does_not(self, stats_mod):
        w = stats_mod.TRADE_WINDOW_TICKS
        k = kill(1000, 1, "e1", "me", aside="ct", vside="t")
        assert stats_mod.is_trade(k, [kill(1000 + w + 1, 1, "mate", "e1",
                                           aside="t", vside="ct")]) is False

    def test_an_enemy_killing_my_killer_is_not_a_trade(self, stats_mod):
        k = kill(1000, 1, "e1", "me", aside="ct", vside="t")
        later = [kill(1100, 1, "e2", "e1", aside="ct", vside="ct")]
        assert stats_mod.is_trade(k, later) is False

    def test_missing_sides_fall_back_to_counting_it(self, stats_mod):
        """Older parses have no side data; assume the avenger was a teammate."""
        k = kill(1000, 1, "e1", "me")
        assert stats_mod.is_trade(k, [kill(1100, 1, "mate", "e1")]) is True

    def test_the_killer_falling_off_the_map_is_not_a_trade(self, stats_mod):
        k = kill(1000, 1, "e1", "me", aside="ct", vside="t")
        assert stats_mod.is_trade(k, [kill(1100, 1, None, "e1")]) is False


# ---------------------------------------------------------------------------
class TestAggregation:
    def _deaths(self, analyze_mod, spec):
        kills = [kill(1000 * i + 100, i + 1, "e1", "me", aside="ct", vside="t",
                      vplace=vp, aplace=ap)
                 for i, (vp, ap) in enumerate(spec)]
        return analyze_mod.analyze(make_match(kills, pov="me"))

    def test_place_rollup_survives_varied_angles(self, analyze_mod):
        """
        Five deaths in Main from four angles is ONE habit. Grouping only by
        (place, angle) splits it into four rows of one and hides it entirely.
        """
        a = self._deaths(analyze_mod, [("Main", "Canal"), ("Main", "Fountain"),
                                       ("Main", "Heaven"), ("Main", "Main"),
                                       ("Main", "Main")])
        top = a["death_places"][0]
        assert top["place"] == "Main" and top["deaths"] == 5
        assert {f["place"] for f in top["from"]} == {"Canal", "Fountain", "Heaven", "Main"}

    def test_place_rollup_needs_more_than_one_death(self, analyze_mod):
        a = self._deaths(analyze_mod, [("Main", "Canal"), ("Palace", "Connector")])
        assert a["death_places"] == []

    def test_hotspot_pairs_your_spot_with_theirs(self, analyze_mod):
        a = self._deaths(analyze_mod, [("Palace", "Connector"),
                                       ("Palace", "Connector"),
                                       ("Palace", "Heaven")])
        top = a["death_hotspots"][0]
        assert (top["place"], top["from"], top["deaths"]) == ("Palace", "Connector", 2)

    def test_deaths_without_a_callout_are_skipped_not_bucketed_as_none(self, analyze_mod):
        kills = [kill(100, 1, "e1", "me", aside="ct", vside="t"),
                 kill(1100, 2, "e1", "me", aside="ct", vside="t")]
        a = analyze_mod.analyze(make_match(kills, pov="me"))
        assert a["death_places"] == [] and a["death_hotspots"] == []

    def test_untraded_count_tracks_the_trade_rule(self, analyze_mod):
        kills = [
            kill(100, 1, "e1", "me", aside="ct", vside="t", vplace="Main"),
            kill(150, 1, "mate", "e1", aside="t", vside="ct"),      # traded
            kill(1100, 2, "e1", "me", aside="ct", vside="t", vplace="Main"),
        ]
        a = analyze_mod.analyze(make_match(kills, pov="me"))
        assert a["death_places"][0]["deaths"] == 2
        assert a["death_places"][0]["untraded"] == 1


# ---------------------------------------------------------------------------
class TestPhaseProfile:
    def _phase_at(self, analyze_mod, seconds, plant=None):
        m = make_match([kill(int(64 * seconds), 1, "e1", "me",
                             aside="ct", vside="t")], pov="me", bomb_plant=plant)
        return next(iter(analyze_mod.analyze(m)["phase_profile"]))

    def test_early_mid_late_boundaries(self, analyze_mod):
        early, mid = analyze_mod.PHASE_EARLY_MAX, analyze_mod.PHASE_MID_MAX
        assert self._phase_at(analyze_mod, early) == "early"
        assert self._phase_at(analyze_mod, early + 1) == "mid"
        assert self._phase_at(analyze_mod, mid) == "mid"
        assert self._phase_at(analyze_mod, mid + 1) == "late"

    def test_post_plant_outranks_the_clock(self, analyze_mod):
        """After the plant the clock stops meaning what it meant before it."""
        assert self._phase_at(analyze_mod, 5, plant=64) == "post_plant"


# ---------------------------------------------------------------------------
class TestReliabilityGate:
    """
    The gate narrate.py uses for flash assists, reused for callouts. Its job is
    to stop the model diagnosing a habit from a single data point.
    """

    def test_one_occurrence_is_never_notable(self, analyze_mod):
        assert analyze_mod.reliable(1, 20, min_count=2, min_frac=0.12,
                                    min_sources=1, sources=1) is False

    def test_a_repeated_pattern_is(self, analyze_mod):
        assert analyze_mod.reliable(4, 20, min_count=2, min_frac=0.12,
                                    min_sources=1, sources=1) is True

    def test_absolute_floor_applies_on_tiny_samples(self, analyze_mod):
        """12% of 3 deaths is 0.36 - the min_count floor has to carry it."""
        assert analyze_mod.reliable(1, 3, min_count=2, min_frac=0.12,
                                    min_sources=1, sources=1) is False

    def test_single_source_fails_the_default_gate(self, analyze_mod):
        assert analyze_mod.reliable(10, 10, sources=1) is False


# ---------------------------------------------------------------------------
class TestRealSlice:
    """Against rounds 1-5 of 2v5.dem, with the current schema."""

    @pytest.fixture
    def a(self, analyze_mod, real_match):
        return analyze_mod.analyze(real_match)

    def test_finds_the_pov_players_five_deaths(self, a):
        assert a["player"] == "pavloven"
        assert a["totals"]["deaths"] == 5

    def test_every_death_is_placed(self, a):
        assert all(d["victim_place"] for d in a["deaths"])
        assert all(d["attacker_place"] for d in a["deaths"])

    def test_alive_counts_are_sane(self, a):
        """Nobody dies in a 0vN, and CS2 tops out at 5 a side."""
        for d in a["deaths"]:
            assert 1 <= d["alive_own"] <= 5
            assert 1 <= d["alive_enemy"] <= 5

    def test_seconds_land_inside_a_real_round(self, a):
        for d in a["deaths"]:
            assert 0 <= d["t"] <= 180

    def test_last_man_deaths_carry_no_teammate_distance(self, a):
        for d in a["deaths"]:
            if d["alive_own"] == 1:
                assert "nearest_mate" not in d
            else:
                assert d.get("nearest_mate") is not None

    def test_totals_agree_with_the_per_death_records(self, a):
        assert a["totals"]["untraded"] == sum(1 for d in a["deaths"] if not d["traded"])
        assert a["totals"]["repeek_deaths"] == sum(1 for d in a["deaths"] if d["repeek"])

    def test_phase_buckets_account_for_every_death(self, a):
        assert sum(p["deaths"] for p in a["phase_profile"].values()) == len(a["deaths"])

    def test_isolation_only_counts_measured_deaths(self, a):
        prof = a["isolation_profile"]
        measured = sum(1 for d in a["deaths"] if d.get("nearest_mate") is not None)
        assert prof["deaths_measured"] == measured

    def test_analysis_can_target_a_non_pov_player(self, analyze_mod, real_match):
        other = analyze_mod.analyze(real_match, player="RENAT4IK")
        assert other["player"] == "RENAT4IK"
        assert other["totals"]["deaths"] > 0


# ---------------------------------------------------------------------------
class TestSnapshot:
    """
    The live "ask about this moment" endpoint's view of one tick. Its defining
    property is what it LEAVES OUT.
    """

    def _match(self):
        fr = frame(192,
                   fplayer("me", 0, 0, "ct", place=0, hp=80),
                   fplayer("mate", 400, 0, "ct", place=1),
                   fplayer("farmate", 2000, 0, "ct", place=2),
                   fplayer("e1", 3000, 0, "t", place=3),
                   fplayer("e2", 3100, 0, "t", place=3),
                   fplayer("deadenemy", 50, 0, "t", place=3, hp=0, alive=False),
                   rnd=1)
        m = make_match([kill(150, 1, "mate", "deadenemy", aside="ct", vside="t",
                             vplace="Palace", aplace="Connector")],
                       [fr], place_names=["MidDoors", "Middle", "Main", "Palace"],
                       pov="me")
        return m

    def test_reports_my_own_callout_and_health(self, analyze_mod):
        s = analyze_mod.snapshot(self._match(), 200)
        assert s["my_place"] == "MidDoors" and s["my_hp"] == 80 and s["alive"] is True

    def test_teammates_come_with_callouts_and_distances(self, analyze_mod):
        s = analyze_mod.snapshot(self._match(), 200)
        assert [m["name"] for m in s["teammates"]] == ["mate", "farmate"]
        assert s["teammates"][0]["place"] == "Middle"
        assert s["teammates"][0]["dist"] == 400

    def test_teammates_are_sorted_nearest_first(self, analyze_mod):
        s = analyze_mod.snapshot(self._match(), 200)
        dists = [m["dist"] for m in s["teammates"]]
        assert dists == sorted(dists)

    def test_enemy_positions_are_never_included(self, analyze_mod):
        """
        The whole point. Coaching a decision using information the player
        couldn't have had is worse than not coaching it - it produces advice
        that is unfollowable in the moment it describes.

        Asserted against the enemies' actual CALLOUT rather than their names,
        because a name can leak legitimately (the round history says who died)
        while a position can't. An earlier version of this test checked for the
        names and passed against a build that leaked positions.
        """
        s = analyze_mod.snapshot(self._match(), 200)
        assert s["enemies_alive"] == 2
        assert "enemies" not in s
        # "Palace" is where both living enemies are standing, and it appears
        # nowhere else in this fixture's frame data.
        blob = json.dumps(s["teammates"]) + json.dumps(
            {k: v for k, v in s.items() if k != "round_so_far"})
        assert "Palace" not in blob

    def test_an_enemy_is_never_described_even_as_a_teammate(self, analyze_mod):
        """Guards the side test itself, not just the output shape."""
        s = analyze_mod.snapshot(self._match(), 200)
        assert {m["name"] for m in s["teammates"]} == {"mate", "farmate"}

    def test_dead_players_count_for_neither_side(self, analyze_mod):
        s = analyze_mod.snapshot(self._match(), 200)
        assert s["enemies_alive"] == 2 and s["teammates_alive"] == 2

    def test_round_history_stops_at_the_asked_tick(self, analyze_mod):
        """Nothing after this instant - the player can't see the future either."""
        m = self._match()
        m["kills"].append(kill(9000, 1, "e1", "me", aside="t", vside="ct",
                               vplace="MidDoors", aplace="Middle"))
        s = analyze_mod.snapshot(m, 200)
        assert [k["victim"] for k in s["round_so_far"]] == ["deadenemy"]

    def test_history_marks_whose_loss_each_kill_was(self, analyze_mod):
        s = analyze_mod.snapshot(self._match(), 200)
        assert s["round_so_far"][0]["mine"] is False

    def test_returns_none_when_there_are_no_frames(self, analyze_mod):
        m = self._match()
        m["frames"] = []
        assert analyze_mod.snapshot(m, 200) is None


class TestMalformedInput:
    def test_no_frames_at_all_still_classifies_deaths(self, analyze_mod):
        """Positions are optional context, not a precondition."""
        m = make_match([kill(100, 1, "e1", "me", aside="ct", vside="t",
                             vplace="Main")], pov="me")
        d = analyze_mod.analyze(m)["deaths"][0]
        assert d["victim_place"] == "Main" and d["alive_own"] == 0

    def test_no_kills_produces_empty_analysis_not_a_crash(self, analyze_mod):
        a = analyze_mod.analyze(make_match([], round_nums=[1], pov="me"))
        assert a["deaths"] == [] and a["death_places"] == []

    def test_missing_round_table_does_not_crash(self, analyze_mod):
        m = make_match([kill(100, 1, "e1", "me", aside="ct", vside="t")], pov="me")
        m["rounds"] = []
        assert analyze_mod.analyze(m)["deaths"][0]["t"] is None

    def test_unknown_player_yields_nothing_rather_than_erroring(self, analyze_mod):
        m = make_match([kill(100, 1, "e1", "me", aside="ct", vside="t")], pov="me")
        assert analyze_mod.analyze(m, player="nobody")["totals"]["deaths"] == 0
