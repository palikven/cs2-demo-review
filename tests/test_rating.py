"""
Tests for HLTV Rating, KAST and their inputs.

Rating 1.0's formula is published, so these check the real thing rather than
something that merely looks plausible: several cases are worked out by hand
from the formula and compared, and the multi-kill term is checked to actually
be quadratic (an ace must beat five one-kill rounds, or the metric is just
kills again with extra steps).

Rating 2.0 is a different matter - HLTV has never published it. What we export
is a community regression, so the tests here pin that it is clearly separated
from the real figure and that it refuses to guess when its inputs are missing,
NOT that it reproduces any particular HLTV number.
"""

import pytest

from conftest import kill, players, rounds


def only(stats, name):
    return stats["per_player"][name]


class TestRatingFormula:
    def test_matches_a_hand_computed_value(self, compute, stats_mod):
        """
        One player, 10 rounds, 10 kills spread one per round, 5 deaths.

            kill     = (10/10) / 0.679          = 1.47275
            survival = ((10-5)/10) / 0.317      = 1.57729
            multi    = (10*1 / 10) / 1.277      = 0.78309
            rating   = (1.47275 + 0.7*1.57729 + 0.78309) / 2.7
        """
        ks = [kill(100 + r * 1000, r, "hero", f"foe{r}") for r in range(1, 11)]
        ks += [kill(500 + r * 1000, r, f"foe{r}", "hero") for r in range(1, 6)]
        s = compute(ks, player_names=["hero"] + [f"foe{r}" for r in range(1, 11)],
                    round_nums=list(range(1, 11)))
        expected = (1.0 / 0.679 + 0.7 * (0.5 / 0.317) + (10 / 10) / 1.277) / 2.7
        assert only(s, "hero")["rating"] == pytest.approx(expected, abs=0.001)

    def test_published_constants_are_unchanged(self, stats_mod):
        """These are HLTV's league averages; changing one silently reprices
        every rating the project has ever produced."""
        assert stats_mod.AVERAGE_KPR == 0.679
        assert stats_mod.AVERAGE_SPR == 0.317
        assert stats_mod.AVERAGE_RMK == 1.277

    def test_multi_kill_weights_are_quadratic(self, stats_mod):
        """n^2 is what makes a 3K worth more than three separate 1Ks."""
        assert stats_mod.MULTI_KILL_WEIGHTS == {1: 1, 2: 4, 3: 9, 4: 16, 5: 25}

    def test_an_ace_beats_five_single_kill_rounds(self, compute):
        """
        Same kills, same deaths, same rounds - only the distribution differs.
        If these come out equal the multi-kill term is not being applied.
        """
        rns = list(range(1, 11))
        spread = [kill(100 + r * 1000, r, "spread", f"a{r}") for r in range(1, 6)]
        acer = [kill(100 + i * 10, 1, "acer", f"b{i}") for i in range(5)]

        s1 = compute(spread, player_names=["spread"] + [f"a{r}" for r in range(1, 6)],
                     round_nums=rns)
        s2 = compute(acer, player_names=["acer"] + [f"b{i}" for i in range(5)],
                     round_nums=rns)
        assert only(s1, "spread")["kills"] == only(s2, "acer")["kills"] == 5
        assert only(s2, "acer")["rating"] > only(s1, "spread")["rating"]

    def test_an_average_statline_lands_near_one(self, compute):
        """
        A player at ALL THREE league averages should rate about 1.00 - the
        sanity check that catches a constant used upside down.

        Getting there needs the multi-kill distribution too, which is the part
        that is easy to miss: 68 kills over 100 rounds is exactly the average
        0.679 KPR, but taken as 68 separate single kills the weighted term is
        0.68 against an average of 1.277, and the player rates 0.83. Average
        players do not frag in a flat line. 20x1K + 18x2K + 4x3K is the same
        68 kills with a weighted total of 128, i.e. 1.28 per round.
        """
        rns = list(range(1, 101))
        ks, rnd, tick = [], 1, 0
        for count, times in ((1, 20), (2, 18), (3, 4)):
            for _ in range(times):
                for i in range(count):
                    tick += 100
                    ks.append(kill(tick, rnd, "avg", f"v{rnd}_{i}"))
                rnd += 1
        # 68 deaths over 100 rounds = the 0.317 average survival rate
        for r in range(50, 118):
            tick += 100
            ks.append(kill(tick, r if r <= 100 else 100, f"x{r}", "avg"))

        s = compute(ks, round_nums=rns)
        avg = only(s, "avg")
        assert avg["kills"] == 68
        assert avg["deaths"] == 68
        assert avg["rating"] == pytest.approx(1.0, abs=0.05)

    def test_flat_fragging_rates_below_the_same_kills_with_multi_kills(self, compute):
        """
        The corollary of the test above, stated on its own so the reason it
        needs a distribution is not buried in a docstring: identical kills and
        identical deaths, only the shape differs.
        """
        rns = list(range(1, 101))
        deaths = [kill(90000 + i * 100, min(50 + i, 100), f"x{i}", "flat")
                  for i in range(68)]

        flat = [kill(100 + r * 1000, r, "flat", f"v{r}") for r in range(1, 69)]
        lumpy, rnd, tick = [], 1, 0
        for count, times in ((1, 20), (2, 18), (3, 4)):
            for _ in range(times):
                for i in range(count):
                    tick += 100
                    lumpy.append(kill(tick, rnd, "flat", f"w{rnd}_{i}"))
                rnd += 1

        a = only(compute(flat + deaths, round_nums=rns), "flat")
        b = only(compute(lumpy + deaths, round_nums=rns), "flat")
        assert a["kills"] == b["kills"] == 68
        assert a["deaths"] == b["deaths"] == 68
        assert b["rating"] > a["rating"]

    def test_more_deaths_lowers_the_rating(self, compute):
        rns = list(range(1, 11))
        base = [kill(100 + r * 1000, r, "p", f"v{r}") for r in range(1, 6)]
        extra = base + [kill(500 + r * 1000, r, f"v{r}", "p") for r in range(1, 6)]
        names = ["p"] + [f"v{r}" for r in range(1, 6)]
        s1 = compute(base, player_names=names, round_nums=rns)
        s2 = compute(extra, player_names=names, round_nums=rns)
        assert only(s2, "p")["rating"] < only(s1, "p")["rating"]

    def test_surviving_without_fragging_still_scores(self, compute):
        """Staying alive is worth something on its own."""
        ks = [kill(100 + r * 1000, r, "other", f"v{r}") for r in range(1, 6)]
        s = compute(ks, player_names=["ghost", "other"] + [f"v{r}" for r in range(1, 6)],
                    round_nums=list(range(1, 11)))
        g = only(s, "ghost")
        assert g["kills"] == 0 and g["deaths"] == 0
        assert g["rating"] > 0

    def test_no_kills_and_dead_every_round_is_exactly_zero(self, compute):
        """
        Every term is zero, so the rating is too - and 0.0 is a real value
        here, not a missing one. The fixture contains exactly this case, so
        anything asserting rating > 0 is wrong rather than strict.
        """
        ks = [kill(100 + r * 1000, r, "killer", "sufferer") for r in range(1, 6)]
        s = compute(ks, player_names=["killer", "sufferer"], round_nums=list(range(1, 6)))
        assert only(s, "sufferer")["rating"] == 0.0


class TestKAST:
    """K, A, S and T each independently make a round count."""

    def test_a_kill_counts(self, compute):
        """Round 1 by the kill, round 2 by surviving it - both count."""
        s = compute([kill(100, 1, "p", "v")], player_names=["p", "v"], round_nums=[1, 2])
        assert only(s, "p")["kast"] == 100.0

    def test_a_kill_makes_an_otherwise_lost_round_count(self, compute):
        """Isolate the K: the player dies in the round but also gets a kill."""
        ks = [kill(100, 1, "p", "enemy"), kill(200, 1, "enemy2", "p")]
        s = compute(ks, player_names=["p", "enemy", "enemy2"], round_nums=[1])
        assert only(s, "p")["kast"] == 100.0

    def test_an_assist_counts(self, compute):
        """
        The assister must DIE in the round, otherwise surviving would earn the
        KAST anyway and the assist path is never exercised - which is exactly
        what an earlier version of this test failed to notice.
        """
        ks = [
            kill(100, 1, "shooter", "v", assister="helper"),
            kill(200, 1, "avenger", "helper"),          # helper dies, untraded
        ]
        s = compute(ks, player_names=["shooter", "helper", "v", "avenger"],
                    round_nums=[1])
        assert only(s, "helper")["deaths"] == 1
        assert only(s, "helper")["kast"] == 100.0, "the assist should carry the round"

    def test_the_assist_credits_the_assister_not_the_shooter(self, compute):
        """A round where the shooter got the kill anyway proves nothing; this
        checks the credit lands on the right player."""
        ks = [
            kill(100, 1, "shooter", "v", assister="helper"),
            kill(200, 1, "avenger", "helper"),
        ]
        s = compute(ks, player_names=["shooter", "helper", "v", "avenger"], round_nums=[1])
        assert only(s, "helper")["assists"] == 1
        assert only(s, "shooter")["assists"] == 0

    def test_surviving_counts(self, compute):
        ks = [kill(100, 1, "a", "b")]
        s = compute(ks, player_names=["a", "b", "bystander"], round_nums=[1])
        assert only(s, "bystander")["kast"] == 100.0

    def test_dying_untraded_does_not_count(self, compute):
        ks = [kill(100, 1, "killer", "victim")]
        s = compute(ks, player_names=["killer", "victim"], round_nums=[1])
        assert only(s, "victim")["kast"] == 0.0

    def test_being_traded_counts(self, compute):
        """The T: dying is forgiven if a teammate trades you."""
        ks = [
            kill(100, 1, "enemy", "victim", aside="ct", vside="t"),
            kill(200, 1, "mate", "enemy", aside="t", vside="ct"),   # inside 320
        ]
        s = compute(ks, player_names=["enemy", "victim", "mate"], round_nums=[1])
        assert only(s, "victim")["kast"] == 100.0

    def test_a_late_trade_does_not_count(self, compute):
        ks = [
            kill(100, 1, "enemy", "victim", aside="ct", vside="t"),
            kill(100 + 400, 1, "mate", "enemy", aside="t", vside="ct"),  # past 320
        ]
        s = compute(ks, player_names=["enemy", "victim", "mate"], round_nums=[1])
        assert only(s, "victim")["kast"] == 0.0

    def test_a_round_with_no_kills_counts_for_everyone(self, compute):
        """
        A full save or a slow eco has no kill events at all, so it never
        appears in the by-round grouping. Forgetting it drags every player's
        KAST down by a round they did nothing wrong in.
        """
        ks = [kill(100, 1, "a", "b")]
        s = compute(ks, player_names=["a", "b"], round_nums=[1, 2, 3])
        # b died in round 1 but survived the two empty rounds
        assert only(s, "b")["kast"] == pytest.approx(66.7, abs=0.1)

    def test_kast_never_exceeds_one_hundred(self, compute):
        """Kill AND assist AND survive in one round is still one round."""
        ks = [kill(100, 1, "p", "v1"), kill(200, 1, "mate", "v2", assister="p")]
        s = compute(ks, player_names=["p", "mate", "v1", "v2"], round_nums=[1])
        assert only(s, "p")["kast"] == 100.0


class TestPerRoundRates:
    def test_kpr_dpr_apr(self, compute):
        ks = [kill(100 + r * 1000, r, "p", f"v{r}") for r in range(1, 5)]
        ks += [kill(500 + r * 1000, r, f"v{r}", "p") for r in range(1, 3)]
        ks += [kill(700, 1, "mate", "x", assister="p")]
        s = compute(ks, player_names=["p", "mate", "x"] + [f"v{r}" for r in range(1, 5)],
                    round_nums=list(range(1, 11)))
        p = only(s, "p")
        assert p["kpr"] == pytest.approx(0.4)
        assert p["dpr"] == pytest.approx(0.2)
        assert p["apr"] == pytest.approx(0.1)


class TestRating2Estimate:
    def _with_damage(self, stats_mod):
        """compute_stats with real damage, so ADR exists and the estimate is
        actually computed rather than short-circuited to None."""
        ks = [kill(100 + r * 1000, r, "p", "v") for r in range(1, 6)]
        dmg = [{"attacker": "p", "victim": "v", "attacker_side": "t",
                "victim_side": "ct", "dmg": 100} for _ in range(10)]
        return stats_mod.compute_stats(
            ks, rounds(*range(1, 11)), players("p", "v"), None, {}, damages=dmg)

    def test_it_is_exported_under_a_name_that_says_it_is_an_estimate(self, stats_mod):
        """
        HLTV never published Rating 2.0. Exporting a guess as `rating2` would
        invite it being quoted as HLTV's own number.

        This has to run on a player who HAS damage - with ADR None the value
        short-circuits and the naming of the computed branch is never checked.
        """
        p = self._with_damage(stats_mod)["per_player"]["p"]
        assert p["adr"] is not None, "fixture must exercise the computed branch"
        assert p["rating2_legacy_est"] is not None
        assert "rating2" not in p, "must not look like HLTV's official figure"

    def test_the_estimate_is_in_a_believable_range(self, stats_mod):
        p = self._with_damage(stats_mod)["per_player"]["p"]
        assert 0 < p["rating2_legacy_est"] < 3

    def test_the_two_ratings_are_separate_numbers(self, stats_mod):
        """Same player, two different formulas - if they were identical one of
        them is not being computed."""
        p = self._with_damage(stats_mod)["per_player"]["p"]
        assert p["rating"] != p["rating2_legacy_est"]

    def test_it_is_none_without_damage_data(self, compute):
        """ADR is one of its inputs; with no damage there is nothing to
        estimate from, and a number here would be confidently wrong."""
        ks = [kill(100, 1, "p", "v")]
        s = compute(ks, player_names=["p", "v"], round_nums=[1])
        assert only(s, "p")["adr"] is None
        assert only(s, "p")["rating2_legacy_est"] is None

    def test_the_real_rating_is_still_produced_without_damage(self, compute):
        """Rating 1.0 does not depend on ADR, so it must survive."""
        ks = [kill(100, 1, "p", "v")]
        s = compute(ks, player_names=["p", "v"], round_nums=[1])
        assert only(s, "p")["rating"] > 0


class TestAgainstRealData:
    def test_every_player_has_a_plausible_rating(self, real_stats):
        """
        Zero is a legitimate floor - the fixture has a player with no kills who
        died in all five rounds - so the lower bound is inclusive.
        """
        for name, s in real_stats["per_player"].items():
            assert 0 <= s["rating"] < 3, f"{name} rated {s['rating']}"

    def test_kast_is_a_percentage(self, real_stats):
        for name, s in real_stats["per_player"].items():
            assert 0 <= s["kast"] <= 100, f"{name} KAST {s['kast']}"

    def test_rating_order_is_not_just_kill_order(self, real_stats):
        """
        If these agreed exactly, the rating would be adding nothing over the
        raw frag count and something is wrong with the deaths or multi-kill
        terms.
        """
        per = real_stats["per_player"]
        by_rating = [n for n, _ in sorted(per.items(), key=lambda kv: -kv[1]["rating"])]
        by_kills = [n for n, _ in sorted(per.items(), key=lambda kv: -kv[1]["kills"])]
        assert by_rating != by_kills

    def test_the_top_rated_player_is_not_the_one_with_most_deaths(self, real_stats):
        per = real_stats["per_player"]
        top = max(per.items(), key=lambda kv: kv[1]["rating"])[0]
        worst = max(per.items(), key=lambda kv: kv[1]["deaths"])[0]
        assert top != worst
