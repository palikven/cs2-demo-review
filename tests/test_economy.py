"""
Tests for round economy and what a death costs in guns.

Three things here turn into confident wrong answers rather than obvious ones:

  * reading the buy a tick too early. During freeze time people are still
    buying, so a side sampled at round start looks like an eco every round.
  * a hard-coded five players per side. A 4v5 round is not an eco because
    somebody disconnected, but divided by five it reads as one.
  * saying the enemy picked the gun up. They often do; it is not in the demo,
    and a model handed that sentence will state it as something that happened.

The last one is the reason the wording is tested and not just the numbers.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import economy  # noqa: E402
from conftest import fplayer, frame, kill, make_match  # noqa: E402


def buy_match(rows, tickrate=64):
    """
    rows: [(round, freeze_end, [(name, side, loadout_idx, armour)]), ...]
    Loadout 0 is a $200 pistol, 1 an $2700 AK, 2 a $4750 AWP.
    """
    frames, rounds = [], []
    for rnd, fe, players in rows:
        rounds.append({"round_num": rnd, "start": fe - 100, "freeze_end": fe,
                       "end": fe + 5000, "official_end": fe + 5200,
                       "winner": "t", "reason": "ct_killed"})
        # one frame inside freeze time and one after, so a reader that takes
        # the wrong side of freeze_end gets a different answer
        for tick, idx_shift in ((fe - 50, True), (fe + 10, False)):
            entries = []
            for name, side, eq, ar in players:
                p = fplayer(name, side=side)
                p["eq"] = 0 if idx_shift else eq       # still buying
                p["ar"] = 0 if idx_shift else ar
                entries.append(p)
            frames.append(frame(tick, *entries, rnd=rnd))
    m = make_match([], frames, pov=players[0][0])
    m["rounds"] = rounds
    m["loadout_values"] = [200, 2700, 4750]
    m["armor_prices"] = {"vest": 650, "helmet_extra": 350, "full_buy": 3700}
    m["players"] = [{"name": n, "steamid": f"s_{n}"}
                    for n in {p[0] for _, _, ps in rows for p in ps}]
    return m


class TestBuyState:
    def test_it_reads_the_buy_after_freeze_time_not_during(self):
        """
        Players are still buying during freeze time. Sampled a tick early every
        side in the match is an eco - which looks like data, not like a bug.
        """
        m = buy_match([(3, 1000, [("a", "t", 1, 1), ("b", "ct", 1, 1)])])
        state = economy.buy_state(m)[3]
        assert state["t"]["value"] == 2700 + 650
        assert state["t"]["state"] != "eco"

    def test_the_denominator_is_the_players_actually_on_that_side(self):
        """
        A 4v5 round is not an eco because someone disconnected. Divided by a
        hard-coded five, a full-buy four-man side reads as 80% and slides a
        category.
        """
        four = [("a", "t", 1, 1), ("b", "t", 1, 1), ("c", "t", 1, 1),
                ("d", "t", 1, 1), ("e", "ct", 1, 1)]
        # round 3, not 1: round one is a pistol round whatever anyone is
        # carrying, so the buy classification there is never exercised.
        m = buy_match([(3, 1000, four)])
        t = economy.buy_state(m)[3]["t"]
        assert t["players"] == 4
        assert t["fraction"] == pytest.approx((2700 + 650) / 3700, abs=0.01)
        assert t["state"] == "full"

    def test_an_eco_and_a_full_buy_are_told_apart(self):
        m = buy_match([(2, 1000, [("a", "t", 0, 0), ("b", "ct", 2, 1)])])
        s = economy.buy_state(m)[2]
        assert s["t"]["state"] == "eco"
        assert s["ct"]["state"] == "full"

    def test_armour_counts_toward_the_buy(self):
        bare = buy_match([(3, 1000, [("a", "t", 1, 0)])])
        kitted = buy_match([(3, 1000, [("a", "t", 1, 1)])])
        assert (economy.buy_state(kitted)[3]["t"]["value"]
                - economy.buy_state(bare)[3]["t"]["value"] == 650)

    def test_a_round_with_no_frames_is_skipped_not_zeroed(self):
        """A missing round must not read as an eco - zero is a real buy state."""
        m = buy_match([(1, 1000, [("a", "t", 1, 1)])])
        m["rounds"].append({"round_num": 9, "start": 0, "freeze_end": 99999,
                            "end": 10 ** 9, "official_end": 10 ** 9,
                            "winner": "t", "reason": "ct_killed"})
        assert 9 not in economy.buy_state(m)

    def test_the_real_match_lands_where_a_human_would_put_it(self, real_match):
        states = economy.buy_state(real_match)
        assert states, "no rounds priced"
        assert all(0 <= s["fraction"] <= 2.0
                   for r in states.values() for s in r.values())


class TestPistolRounds:
    def test_round_one_is_a_pistol_round(self, real_match):
        assert 1 in economy.pistol_rounds(real_match)

    def test_the_second_half_opener_is_found_not_assumed(self):
        """
        The format is not in the demo. MR12, MR15 and overtime all put the swap
        somewhere different, so it is detected from the sides flipping.
        """
        rows = [(r, r * 10000,
                 [("a", "t" if r < 8 else "ct", 0, 0),
                  ("b", "ct" if r < 8 else "t", 0, 0)])
                for r in range(1, 12)]
        assert economy.pistol_rounds(buy_match(rows)) == {1, 8}

    def test_a_pistol_round_is_not_called_an_eco(self):
        """Everyone is on a pistol by rule, so buy discipline does not apply."""
        rows = [(1, 1000, [("a", "t", 0, 0), ("b", "ct", 0, 0)])]
        assert economy.buy_state(buy_match(rows))[1]["t"]["state"] == "pistol"


class TestDuelValue:
    def test_it_prices_both_sides_of_the_duel(self):
        k = dict(kill(100, 1, "a", "b"),
                 attacker_inv=["Desert Eagle", "knife"],
                 victim_inv=["AK-47", "Glock-18"])
        v = economy.duel_value({}, k)
        assert v == {"lost": 2700, "risked": 700, "delta": 2000,
                     "armed_them": True}

    def test_an_even_trade_is_not_flagged(self):
        k = dict(kill(100, 1, "a", "b"),
                 attacker_inv=["AK-47"], victim_inv=["M4A1-S"])
        assert economy.duel_value({}, k)["armed_them"] is False

    def test_a_pistol_lost_to_a_pistol_is_not_flagged(self):
        """A $200 pistol lost to a knife is a 100% gap and nothing at all."""
        k = dict(kill(100, 1, "a", "b"),
                 attacker_inv=["knife"], victim_inv=["Glock-18"])
        assert economy.duel_value({}, k)["armed_them"] is False

    def test_a_cheap_gun_lost_to_nothing_is_not_flagged(self):
        """
        The absolute floor specifically, which the pistol case cannot reach -
        a $200 loss fails the GAP floor first, so a build with no value floor
        at all still passes that test.

        A $1700 Scout lost to a knife clears the gap easily. It is still not the
        compounding disaster the flag is for: what matters is losing a weapon
        that decides the next round, not any weapon at all.
        """
        k = dict(kill(100, 1, "a", "b"),
                 attacker_inv=["knife"], victim_inv=["SSG 08"])
        v = economy.duel_value({}, k)
        assert v["delta"] >= economy.ARMED_THEM_MIN_GAP, \
            "test setup no longer reaches the value floor"
        assert v["lost"] < economy.ARMED_THEM_MIN_VALUE
        assert v["armed_them"] is False

    def test_an_awp_lost_to_an_smg_is_flagged(self):
        k = dict(kill(100, 1, "a", "b"),
                 attacker_inv=["MP9"], victim_inv=["AWP"])
        assert economy.duel_value({}, k)["armed_them"] is True

    def test_an_unknown_weapon_scores_zero_rather_than_guessing(self):
        k = dict(kill(100, 1, "a", "b"),
                 attacker_inv=["Rocket Launcher"], victim_inv=["AK-47"])
        assert economy.duel_value({}, k)["risked"] == 0

    def test_a_suicide_is_not_a_duel(self):
        assert economy.duel_value({}, kill(100, 1, "a", "a")) is None


class TestProfile:
    def test_it_separates_what_was_lost_from_what_was_taken(self, real_match):
        p = economy.profile(real_match, real_match["pov"])
        assert p["deaths_priced"] > 0 and p["kills_priced"] > 0
        assert p["net"] == p["value_taken"] - p["value_lost"]

    def test_it_never_claims_the_gun_was_picked_up(self, real_match):
        """
        The wording is the test. "Armed them" reads as an event; the demo
        records a death and an inventory, not a pickup.
        """
        p = economy.profile(real_match, real_match["pov"])
        assert "picked up" in p["note"]
        assert "not money" in p["note"]

    def test_the_fact_sheet_repeats_the_warning_where_the_model_will_read_it(
            self, analyze_mod, real_match):
        import coach
        a = analyze_mod.analyze(real_match)
        sheet = coach.build_facts(a, real_match)
        if "ECONOMY" not in sheet:
            pytest.skip("fixture has no priced deaths")
        assert "do not claim they did" in sheet or "do not claim" in sheet.lower()

    def test_a_death_carries_both_sides_buy_state(self, analyze_mod, real_match):
        """
        One side's buy alone invites the wrong conclusion half the time - the
        mistake is the gap, not the number.
        """
        import coach
        a = analyze_mod.analyze(real_match)
        priced = [d for d in a["deaths"] if d.get("buy")]
        if not priced:
            pytest.skip("fixture has no priced deaths")
        for d in priced:
            assert d.get("enemy_buy"), "only one side's buy reached the death"
        line = next(l for l in coach.build_facts(a, real_match).splitlines()
                    if str(priced[0]["tick"]) in l)
        assert "theirs on a" in line

    def test_a_player_not_in_the_match_prices_nothing(self, real_match):
        p = economy.profile(real_match, "nobody")
        assert p["deaths_priced"] == 0 and p["value_lost"] == 0
