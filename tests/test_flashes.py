"""
Tests for flash judgement.

The failure modes here are all ways of producing a number that looks fine:

  * deciding "same team" from anything but the tick. Sides swap at half time, so
    a name-based check flips every verdict in the second half and the output is
    still perfectly well-formed.
  * scoring a flash as good OR bad. One flashbang blinds everyone in front of
    it - in the bundled match a single pop blinded an enemy who was immediately
    killed and a teammate for 4.95s. Any single score is wrong for that flash.
  * counting the 0.2s wash at the edge of the screen, which makes every player
    look equally careless and buries the real habit.
  * dividing follow-up kills by flashes thrown. A flash that stops an angle
    being held worked whether or not anything died, so that rate would score
    good utility as failure.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import flashes  # noqa: E402
from conftest import fplayer, frame, kill, make_match  # noqa: E402


def blind(tick, by, who, d, rnd=1):
    return {"t": tick, "by": by, "who": who, "d": d, "r": rnd}


def flash_match(frames, blinds, kills=()):
    m = make_match(list(kills), frames, pov="me")
    m["utility"] = {"blinds": blinds}
    m["tickrate"] = 64
    return m


class TestClassify:
    def test_a_teammate_and_an_enemy_are_told_apart(self):
        m = flash_match(
            [frame(100, fplayer("me", side="ct"), fplayer("mate", side="ct"),
                   fplayer("foe", side="t"))],
            [blind(100, "me", "mate", 3.0), blind(100, "me", "foe", 3.0)])
        kinds = {r["who"]: r["kind"] for r in flashes.classify(m)}
        assert kinds == {"mate": "team", "foe": "enemy"}

    def test_sides_are_read_at_the_tick_not_from_the_name(self):
        """
        Sides swap at half time. The same two players are teammates in the first
        half and opponents in the second, so a name-keyed lookup gets every
        second-half verdict backwards - and still returns a clean answer.
        """
        m = flash_match(
            [frame(100, fplayer("me", side="ct"), fplayer("x", side="ct")),
             frame(9000, fplayer("me", side="ct"), fplayer("x", side="t"))],
            [blind(100, "me", "x", 3.0), blind(9000, "me", "x", 3.0)])
        got = [r["kind"] for r in flashes.classify(m)]
        assert got == ["team", "enemy"]

    def test_flashing_yourself_is_its_own_kind(self):
        m = flash_match([frame(100, fplayer("me", side="ct"))],
                        [blind(100, "me", "me", 2.0)])
        assert flashes.classify(m)[0]["kind"] == "self"

    def test_an_unresolvable_side_is_unknown_not_enemy(self):
        """
        Defaulting to "enemy" would quietly flatter every player in the match -
        an unreadable blind would become a credit rather than a gap.
        """
        m = flash_match([frame(100, fplayer("me", side="ct"))],
                        [blind(100, "me", "ghost", 3.0)])
        assert flashes.classify(m)[0]["kind"] == "unknown"

    def test_a_blind_with_no_thrower_is_dropped(self):
        m = flash_match([frame(100, fplayer("me", side="ct"))],
                        [{"t": 100, "by": None, "who": "me", "d": 2.0}])
        assert flashes.classify(m) == []


class TestProfile:
    def test_short_washes_do_not_count_as_flashes(self):
        """A 0.2s wash is not a flash. Counting them flattens everyone."""
        m = flash_match(
            [frame(100, fplayer("me", side="ct"), fplayer("mate", side="ct"))],
            [blind(100, "me", "mate", 0.2), blind(101, "me", "mate", 0.3)])
        p = flashes.profile(m, "me")
        assert p["team"] == 2, "the blinds themselves should still be recorded"
        assert p["team_meaningful"] == 0
        assert p["team_share"] is None

    def test_seconds_cover_the_same_blinds_as_the_counts(self):
        """
        A meaningful count beside an all-blinds duration is two populations read
        as one sentence, and nothing in the output says which is which.
        """
        m = flash_match(
            [frame(100, fplayer("me", side="ct"), fplayer("mate", side="ct"))],
            [blind(100, "me", "mate", 0.3), blind(101, "me", "mate", 2.0)])
        p = flashes.profile(m, "me")
        assert p["team_meaningful"] == 1
        assert p["team_seconds"] == 2.0, "the 0.3s wash leaked into the total"

    def test_one_flash_can_be_good_and_bad_at_once(self):
        """
        The reason nothing here is scored. This pop blinded an enemy who died
        for it AND a teammate for five seconds; any single verdict is wrong.
        """
        m = flash_match(
            [frame(100, fplayer("me", side="ct"), fplayer("mate", side="ct"),
                   fplayer("foe", side="t"))],
            [blind(100, "me", "foe", 4.0), blind(100, "me", "mate", 5.0)],
            kills=[kill(150, 1, "mate", "foe")])
        p = flashes.profile(m, "me")
        assert p["enemy_meaningful"] == 1 and p["team_meaningful"] == 1
        assert len(p["follow_ups"]) == 1
        assert len(p["full_team_blinds"]) == 1
        assert "score" in p["note"]

    def test_it_names_who_gets_blinded_most(self):
        fr = [frame(100 + i, fplayer("me", side="ct"), fplayer("a", side="ct"),
                    fplayer("b", side="ct")) for i in range(4)]
        bl = [blind(100, "me", "a", 2.0), blind(101, "me", "a", 2.0),
              blind(102, "me", "a", 2.0), blind(103, "me", "b", 2.0)]
        assert flashes.profile(flash_match(fr, bl), "me")["worst_victim"] \
            == ("a", 3)

    def test_only_long_team_blinds_are_listed_as_full(self):
        m = flash_match(
            [frame(100, fplayer("me", side="ct"), fplayer("mate", side="ct"))],
            [blind(100, "me", "mate", 1.0), blind(101, "me", "mate", 4.0)])
        heavy = flashes.profile(m, "me")["full_team_blinds"]
        assert [h["d"] for h in heavy] == [4.0]


class TestFollowUps:
    def test_a_kill_inside_the_blind_counts(self):
        m = flash_match(
            [frame(100, fplayer("me", side="ct"), fplayer("foe", side="t"))],
            [blind(100, "me", "foe", 3.0)],
            kills=[kill(100 + 64, 1, "me", "foe")])
        assert len(flashes.profile(m, "me")["follow_ups"]) == 1

    def test_a_kill_after_the_blind_wore_off_does_not(self):
        """
        The window is the blind's own duration. Without that it is a fixed
        window and a death ten seconds later gets credited to a flash that had
        long since worn off.
        """
        m = flash_match(
            [frame(100, fplayer("me", side="ct"), fplayer("foe", side="t"))],
            [blind(100, "me", "foe", 1.0)],
            kills=[kill(100 + 64 * 4, 1, "me", "foe")])
        assert flashes.profile(m, "me")["follow_ups"] == []

    def test_it_is_never_expressed_as_a_rate(self, real_match):
        """
        Flashing an angle so nobody can hold it works whether or not anything
        died. A success rate would score that as failure.
        """
        p = flashes.profile(real_match, real_match["pov"])
        assert isinstance(p["follow_ups"], list)
        assert "not a success rate" in p["note"].lower()
        assert not any("rate" in k for k in p if k != "note")


class TestMatchTable:
    def test_every_thrower_gets_a_row(self, real_match):
        """
        One player's team-flash share means nothing without the other nine -
        it is the difference between nagging and a benchmark.
        """
        table = flashes.match_table(real_match)
        if not table:
            pytest.skip("fixture has no blind data")
        assert len(table) > 1
        assert all("team_share" in v for v in table.values())

    def test_the_fact_sheet_carries_the_comparison(self, analyze_mod, real_match):
        import coach
        a = analyze_mod.analyze(real_match)
        if not a.get("flashes"):
            pytest.skip("fixture has no blind data")
        sheet = coach.build_facts(a, real_match)
        assert "FLASHES" in sheet
        assert "for scale" in sheet, "the model got one number with no benchmark"
        assert "NOT a score" in sheet

    def test_a_match_with_no_blind_data_says_nothing_rather_than_zero(
            self, analyze_mod, real_match):
        """
        An older parse has no blinds. Reporting 0 team flashes there is a
        confident wrong answer; saying nothing is the honest one.
        """
        stripped = dict(real_match)
        stripped["utility"] = {k: v for k, v in (real_match.get("utility") or {}).items()
                               if k != "blinds"}
        a = analyze_mod.analyze(stripped)
        assert not a.get("flashes")
        import coach
        assert "FLASHES" not in coach.build_facts(a, stripped)
