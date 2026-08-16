"""
Tests for parse_demo.compute_stats().

Convention used here
--------------------
Tests assert the CORRECT behaviour. Where a defect is known but not yet fixed,
mark it with @pytest.mark.xfail(reason="...", strict=True): the suite stays
green while documenting the defect, and the moment someone fixes it the xfail
flips to a FAILURE, which is the prompt to delete the marker. Nothing rots.

All six bugs the first pass found are now fixed, so no xfail markers remain:
  BUG 1  suicides / world deaths credited as kills          -> is_player_kill()
  BUG 2  suicide opener taking both sides of the duel       -> is_player_kill()
  BUG 3  null-attacker first-kill voiding the opening duel  -> first player kill
  BUG 4  rounds_played counting only rounds with a kill     -> reads the round table
  BUG 5  trade detection with no teammate check             -> side comparison
  BUG 6  ADR not computed                                   -> damages -> adr
"""

import pytest

from conftest import kill, players, rounds


# ===========================================================================
# normal cases
# ===========================================================================
class TestBasicCounting:
    def test_kills_deaths_assists(self, compute):
        ks = [
            kill(100, 1, "alice", "bob"),
            kill(200, 1, "alice", "carol", assister="dave"),
            kill(300, 2, "bob", "alice"),
        ]
        per = compute(ks)["per_player"]
        assert (per["alice"]["kills"], per["alice"]["deaths"]) == (2, 1)
        assert (per["bob"]["kills"], per["bob"]["deaths"]) == (1, 1)
        assert per["carol"]["deaths"] == 1
        assert per["dave"]["assists"] == 1

    def test_headshot_percentage(self, compute):
        ks = [
            kill(100, 1, "alice", "bob", hs=True),
            kill(200, 1, "alice", "carol", hs=True),
            kill(300, 1, "alice", "dave", hs=False),
            kill(400, 1, "alice", "erin", hs=False),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["hs_kills"] == 2
        assert per["alice"]["hs_pct"] == 50.0

    def test_flash_assist_requires_an_assister(self, compute):
        ks = [
            kill(100, 1, "alice", "bob", assister="carol", flash=True),
            kill(200, 1, "alice", "dave", assister="carol", flash=False),
        ]
        per = compute(ks)["per_player"]
        assert per["carol"]["assists"] == 2
        assert per["carol"]["flash_assists"] == 1

    def test_kd_ratio(self, compute):
        ks = [
            kill(100, 1, "alice", "bob"),
            kill(200, 1, "alice", "carol"),
            kill(300, 2, "bob", "alice"),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["kd"] == 2.0
        assert per["bob"]["kd"] == 1.0

    def test_kd_with_zero_deaths_does_not_divide_by_zero(self, compute):
        per = compute([kill(100, 1, "alice", "bob")])["per_player"]
        assert per["alice"]["kd"] == 1.0
        assert isinstance(per["alice"]["kd"], float)

    def test_player_with_no_events_gets_zeroed_row(self, compute):
        ks = [kill(100, 1, "alice", "bob")]
        per = compute(ks, player_names=["alice", "bob", "ghost"])["per_player"]
        assert per["ghost"]["kills"] == 0
        assert per["ghost"]["deaths"] == 0
        assert per["ghost"]["kd"] == 0.0
        assert per["ghost"]["hs_pct"] == 0.0
        assert per["ghost"]["entry_win_pct"] == 0.0


class TestOpeningDuels:
    def test_first_kill_of_round_is_the_opening_duel(self, compute):
        ks = [
            kill(100, 1, "alice", "bob"),
            kill(500, 1, "carol", "dave"),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["entry_success"] == 1
        assert per["alice"]["entry_attempts"] == 1
        assert per["bob"]["opening_deaths"] == 1
        assert per["bob"]["entry_attempts"] == 1
        # the second kill of the round is not an opening duel
        assert per["carol"]["entry_attempts"] == 0
        assert per["dave"]["opening_deaths"] == 0

    def test_opening_duel_uses_tick_order_not_list_order(self, compute):
        """Kill events arriving out of order must not change who opened."""
        ks = [
            kill(900, 1, "carol", "dave"),
            kill(100, 1, "alice", "bob"),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["entry_success"] == 1
        assert per["carol"]["entry_success"] == 0

    def test_entry_win_pct_is_openers_won_over_openers_contested(self, compute):
        ks = [
            kill(100, 1, "alice", "bob"),
            kill(100, 2, "alice", "bob"),
            kill(100, 3, "bob", "alice"),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["entry_attempts"] == 3
        assert per["alice"]["entry_success"] == 2
        assert per["alice"]["entry_win_pct"] == pytest.approx(66.7)

    def test_each_round_contributes_exactly_one_opening_duel(self, compute):
        ks = []
        for r in range(1, 6):
            ks.append(kill(100, r, "alice", "bob"))
            ks.append(kill(200, r, "alice", "carol"))
        per = compute(ks)["per_player"]
        assert per["alice"]["entry_success"] == 5
        assert per["bob"]["opening_deaths"] == 5


class TestTrades:
    def test_death_is_traded_when_killer_dies_inside_the_window(self, compute):
        ks = [
            kill(1000, 1, "enemy", "alice"),
            kill(1200, 1, "bob", "enemy"),  # +200 ticks, teammate avenges
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["traded_deaths"] == 1
        assert per["alice"]["untraded_deaths"] == 0

    def test_death_is_untraded_when_the_revenge_is_too_late(self, compute):
        ks = [
            kill(1000, 1, "enemy", "alice"),
            kill(1400, 1, "bob", "enemy"),  # +400 ticks, outside the 320 window
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["traded_deaths"] == 0
        assert per["alice"]["untraded_deaths"] == 1

    def test_trade_window_boundary_is_inclusive_at_320(self, compute):
        per = compute([
            kill(1000, 1, "enemy", "alice"),
            kill(1320, 1, "bob", "enemy"),
        ])["per_player"]
        assert per["alice"]["traded_deaths"] == 1

    def test_trade_window_excludes_321(self, compute):
        per = compute([
            kill(1000, 1, "enemy", "alice"),
            kill(1321, 1, "bob", "enemy"),
        ])["per_player"]
        assert per["alice"]["untraded_deaths"] == 1

    def test_traded_plus_untraded_equals_deaths(self, compute, real_stats):
        for name, s in real_stats["per_player"].items():
            assert s["traded_deaths"] + s["untraded_deaths"] == s["deaths"], name

    def test_killer_dying_to_his_own_side_is_not_a_trade(self, compute, stats_mod, kcols):
        """
        alice is killed by enemy. enemy then kills himself (or is teamkilled)
        two seconds later. Nobody traded alice - her team got nothing out of it.
        compute_stats() only checks 'did the killer die', so it counts this.
        """
        ks = [
            kill(1000, 1, "enemy", "alice"),
            kill(1100, 1, "enemy", "enemy", weapon="world"),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["traded_deaths"] == 0
        assert per["alice"]["untraded_deaths"] == 1


# ===========================================================================
# edge cases
# ===========================================================================
class TestPlayerDisconnects:
    """
    2v5.dem round 5 is the real thing: two players drop, and awpy emits kill
    rows whose attacker and victim are both null.
    """

    def test_null_attacker_and_victim_do_not_crash(self, compute):
        ks = [
            kill(1000, 1, None, None, weapon="world"),
            kill(1100, 1, "alice", "bob"),
        ]
        out = compute(ks, player_names=["alice", "bob"])
        assert out["per_player"]["alice"]["kills"] == 1

    def test_null_attacker_is_not_credited_to_anyone(self, compute):
        ks = [
            kill(1000, 1, None, "bob"),
            kill(1100, 1, "alice", "carol"),
        ]
        per = compute(ks, player_names=["alice", "bob", "carol"])["per_player"]
        assert sum(p["kills"] for p in per.values()) == 1
        assert per["bob"]["deaths"] == 1  # the death still happened

    def test_disconnected_player_absent_from_roster_is_ignored(self, compute):
        """A kill referencing someone not in the player list must not KeyError."""
        ks = [kill(1000, 1, "ghost", "alice")]
        per = compute(ks, player_names=["alice"])["per_player"]
        assert "ghost" not in per
        assert per["alice"]["deaths"] == 1

    def test_null_opener_falls_through_to_the_real_first_duel(self, compute):
        """
        A world/disconnect event at the top of the round is not an opening duel.
        The opening duel is the first real player-vs-player kill.
        """
        ks = [
            kill(1000, 1, None, None, weapon="world"),
            kill(1500, 1, "alice", "bob"),
        ]
        per = compute(ks, player_names=["alice", "bob"])["per_player"]
        assert per["alice"]["entry_success"] == 1
        assert per["bob"]["opening_deaths"] == 1


class TestSuicides:
    def test_suicide_is_a_death_but_not_a_kill(self, compute):
        ks = [kill(1000, 1, "alice", "alice", weapon="world")]
        per = compute(ks, player_names=["alice"])["per_player"]
        assert per["alice"]["deaths"] == 1
        assert per["alice"]["kills"] == 0

    def test_suicide_opener_is_not_an_opening_duel(self, compute):
        ks = [
            kill(1000, 1, "alice", "alice", weapon="world"),
            kill(1500, 1, "bob", "carol"),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["entry_success"] == 0
        assert per["alice"]["opening_deaths"] == 0
        assert per["bob"]["entry_success"] == 1

    def test_suicide_does_not_produce_a_negative_or_absurd_kd(self, compute):
        ks = [kill(1000, 1, "alice", "alice", weapon="world")]
        per = compute(ks, player_names=["alice"])["per_player"]
        assert per["alice"]["kd"] >= 0


class TestRoundsWithNoKills:
    def test_flawless_round_does_not_crash(self, compute, stats_mod, kcols):
        """A round can end on the bomb or the clock with zero kills."""
        ks = [kill(1000, 1, "alice", "bob")]
        out = stats_mod.compute_stats(
            ks, rounds(1, 2, 3), players("alice", "bob"), None, kcols
        )
        assert out["per_player"]["alice"]["kills"] == 1

    def test_rounds_played_counts_played_rounds(self, stats_mod, kcols):
        ks = [kill(1000, 1, "alice", "bob")]  # rounds 2 and 3 were kill-free
        out = stats_mod.compute_stats(
            ks, rounds(1, 2, 3), players("alice", "bob"), None, kcols
        )
        assert out["rounds_played"] == 3

    def test_no_kills_at_all_yields_a_fully_zeroed_table(self, stats_mod, kcols):
        out = stats_mod.compute_stats([], rounds(1, 2), players("alice", "bob"), None, kcols)
        assert out["per_player"]["alice"]["kills"] == 0
        assert out["per_player"]["alice"]["kd"] == 0.0


class TestOvertime:
    """MR12: regulation is rounds 1-24, overtime starts at 25."""

    def test_overtime_round_numbers_are_handled(self, compute):
        ks = []
        for r in (23, 24, 25, 26, 27, 28):
            ks.append(kill(100, r, "alice", "bob"))
            ks.append(kill(300, r, "alice", "carol"))
        out = compute(ks)
        assert out["rounds_played"] == 6
        assert out["per_player"]["alice"]["kills"] == 12
        assert out["per_player"]["alice"]["entry_success"] == 6

    def test_second_overtime_does_not_wrap_or_collide(self, compute):
        ks = [kill(100, r, "alice", "bob") for r in (25, 31, 37)]
        out = compute(ks)
        assert out["rounds_played"] == 3
        assert out["per_player"]["bob"]["opening_deaths"] == 3

    def test_trade_window_does_not_leak_across_rounds(self, compute):
        """
        Ticks keep climbing across rounds, but two deaths in different rounds
        are never a trade. compute_stats groups by round first, so this holds -
        the test pins it.
        """
        ks = [
            kill(1000, 24, "enemy", "alice"),
            kill(1100, 25, "bob", "enemy"),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["untraded_deaths"] == 1
        assert per["alice"]["traded_deaths"] == 0


class TestMalformedInput:
    def test_kills_with_a_null_round_do_not_crash(self, compute):
        ks = [
            kill(1000, None, "alice", "bob"),
            kill(1100, 1, "alice", "carol"),
        ]
        out = compute(ks, round_nums=[1])
        assert out["per_player"]["alice"]["kills"] == 2

    def test_kill_with_a_null_tick_is_skipped_not_fatal(self, compute):
        ks = [
            kill(None, 1, "alice", "bob"),
            kill(1000, 1, "alice", "carol"),
        ]
        per = compute(ks)["per_player"]
        assert per["alice"]["kills"] == 1  # the tickless row is dropped
        assert per["bob"]["deaths"] == 0

    def test_empty_player_list(self, stats_mod, kcols):
        out = stats_mod.compute_stats(
            [kill(100, 1, "alice", "bob")], rounds(1), [], None, kcols
        )
        assert out["per_player"] == {}


# ===========================================================================
# regression against a real slice of 2v5.dem (rounds 1-5)
# ===========================================================================
class TestRealSlice:
    def test_fixture_shape(self, real_slice):
        assert len(real_slice["kills"]) == 42
        assert len(real_slice["rounds"]) == 5

    def test_every_death_is_classified(self, real_stats):
        for name, s in real_stats["per_player"].items():
            assert s["traded_deaths"] + s["untraded_deaths"] == s["deaths"], name

    def test_derived_rates_are_internally_consistent(self, real_stats):
        for name, s in real_stats["per_player"].items():
            if s["kills"]:
                assert s["hs_pct"] == pytest.approx(100 * s["hs_kills"] / s["kills"], abs=0.05)
            if s["entry_attempts"]:
                assert s["entry_win_pct"] == pytest.approx(
                    100 * s["entry_success"] / s["entry_attempts"], abs=0.05
                )
            assert 0 <= s["hs_pct"] <= 100
            assert 0 <= s["entry_win_pct"] <= 100

    def test_every_death_in_the_slice_is_still_counted(self, real_slice, real_stats):
        """Suicides stop being kills, but they never stop being deaths."""
        total_deaths = sum(s["deaths"] for s in real_stats["per_player"].values())
        null_victim = sum(1 for k in real_slice["kills"] if k["victim"] is None)
        assert total_deaths == len(real_slice["kills"]) - null_victim

    def test_real_kill_total_excludes_suicides(self, real_slice, real_stats):
        total_kills = sum(s["kills"] for s in real_stats["per_player"].values())
        suicides = sum(
            1 for k in real_slice["kills"]
            if k["attacker"] is not None and k["attacker"] == k["victim"]
        )
        null_attacker = sum(1 for k in real_slice["kills"] if k["attacker"] is None)
        assert total_kills == len(real_slice["kills"]) - null_attacker - suicides

    def test_every_round_in_the_slice_has_exactly_one_opening_duel(self, real_slice, real_stats):
        per = real_stats["per_player"]
        opening_kills = sum(s["entry_success"] for s in per.values())
        opening_deaths = sum(s["opening_deaths"] for s in per.values())
        n_rounds = len({k["round"] for k in real_slice["kills"]})
        assert opening_kills == n_rounds
        assert opening_deaths == n_rounds


# ===========================================================================
# damage / ADR
# ===========================================================================
def dmg(attacker, victim, amount, a_side="t", v_side="ct"):
    return {"attacker": attacker, "victim": victim, "dmg": amount,
            "attacker_side": a_side, "victim_side": v_side}


class TestADR:
    def test_adr_is_reported(self, compute):
        per = compute([kill(100, 1, "alice", "bob")])["per_player"]
        assert "adr" in per["alice"]
        assert "damage" in per["alice"]

    def test_adr_is_none_when_no_damage_data_was_parsed(self, compute):
        per = compute([kill(100, 1, "alice", "bob")])["per_player"]
        assert per["alice"]["adr"] is None

    def test_adr_is_damage_over_rounds_played(self, stats_mod, kcols):
        out = stats_mod.compute_stats(
            [kill(100, 1, "alice", "bob")], rounds(1, 2, 3, 4),
            players("alice", "bob"), None, kcols,
            damages=[dmg("alice", "bob", 100), dmg("alice", "bob", 60)],
        )
        assert out["rounds_played"] == 4
        assert out["per_player"]["alice"]["damage"] == 160
        assert out["per_player"]["alice"]["adr"] == 40.0

    def test_team_damage_is_excluded_from_adr(self, stats_mod, kcols):
        out = stats_mod.compute_stats(
            [kill(100, 1, "alice", "enemy")], rounds(1),
            players("alice", "bob", "enemy"), None, kcols,
            damages=[dmg("alice", "enemy", 100),
                     dmg("alice", "bob", 90, a_side="t", v_side="t")],
        )
        assert out["per_player"]["alice"]["damage"] == 100

    def test_self_damage_is_excluded_from_adr(self, stats_mod, kcols):
        out = stats_mod.compute_stats(
            [kill(100, 1, "alice", "enemy")], rounds(1),
            players("alice", "enemy"), None, kcols,
            damages=[dmg("alice", "alice", 45, a_side="t", v_side="t"),
                     dmg("alice", "enemy", 55)],
        )
        assert out["per_player"]["alice"]["damage"] == 55

    def test_damage_from_an_unknown_player_is_ignored(self, stats_mod, kcols):
        out = stats_mod.compute_stats(
            [kill(100, 1, "alice", "enemy")], rounds(1),
            players("alice", "enemy"), None, kcols,
            damages=[dmg("ghost", "alice", 70, a_side="ct", v_side="t")],
        )
        assert out["per_player"]["alice"]["damage"] == 0


class TestTradeSides:
    """Trades now require the trader to be on the dead player's side."""

    def test_teammate_trade_counts(self, stats_mod, kcols):
        ks = [
            {**kill(1000, 1, "enemy", "alice"), "attacker_side": "ct", "victim_side": "t"},
            {**kill(1100, 1, "bob", "enemy"), "attacker_side": "t", "victim_side": "ct"},
        ]
        per = stats_mod.compute_stats(
            ks, rounds(1), players("alice", "bob", "enemy"), None, kcols
        )["per_player"]
        assert per["alice"]["traded_deaths"] == 1

    def test_enemy_killing_the_killer_is_not_a_trade(self, stats_mod, kcols):
        """The killer gets teamkilled. alice's team gained nothing."""
        ks = [
            {**kill(1000, 1, "enemy", "alice"), "attacker_side": "ct", "victim_side": "t"},
            {**kill(1100, 1, "enemy2", "enemy"), "attacker_side": "ct", "victim_side": "ct"},
        ]
        per = stats_mod.compute_stats(
            ks, rounds(1), players("alice", "enemy", "enemy2"), None, kcols
        )["per_player"]
        assert per["alice"]["traded_deaths"] == 0
        assert per["alice"]["untraded_deaths"] == 1

    def test_missing_side_data_falls_back_to_counting_the_trade(self, compute):
        """Older parses have no side columns - don't silently zero out trades."""
        per = compute([
            kill(1000, 1, "enemy", "alice"),
            kill(1100, 1, "bob", "enemy"),
        ])["per_player"]
        assert per["alice"]["traded_deaths"] == 1
