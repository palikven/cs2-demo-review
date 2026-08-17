"""
Tests for role evidence.

The behaviour worth protecting is the REFUSAL. Every number here is a count over
one match, and every one of them is small enough to look like a pattern by
accident: 22 rounds hold 22 opening duels shared across ten players. A module
that names an entry fragger off five opening kills is producing a horoscope.

The second thing under test is the denominator. Utility volume only means
something against the player's own team - a team that saves differently throws
differently. Averaging across the lobby moved the bar by enough to invent a
support on the bundled demo that the correct denominator does not support.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import roles  # noqa: E402
from conftest import fplayer, frame, kill, make_match  # noqa: E402


def match_with(frames_, kills=(), names=None, rounds_played=22):
    m = make_match(list(kills), frames_, pov=None)
    if names:
        m["players"] = [{"name": n, "steamid": f"s_{n}"} for n in names]
    m["stats"]["rounds_played"] = rounds_played
    return m


def sides_frame(tick, t_names, ct_names, rnd=1):
    return frame(tick, *([fplayer(n, side="t") for n in t_names]
                         + [fplayer(n, side="ct") for n in ct_names]), rnd=rnd)


class TestTeams:
    def test_round_one_sides_are_the_teams(self):
        m = match_with([sides_frame(0, ["a", "b"], ["c", "d"])])
        t = roles.teams(m)
        assert t["a"] == t["b"] != t["c"] == t["d"]

    def test_the_half_time_swap_does_not_resplit_them(self):
        """
        `side` is not team identity. Read from a second-half frame, every player
        lands in the wrong team - and the utility denominators follow them.
        """
        m = match_with([sides_frame(0, ["a", "b"], ["c", "d"]),
                        sides_frame(50000, ["c", "d"], ["a", "b"], rnd=13)])
        t = roles.teams(m)
        assert t["a"] == t["b"] != t["c"] == t["d"]

    def test_a_late_joiner_is_placed_by_who_they_play_with(self):
        """
        A substitute absent from round one has no starting side. Placing them by
        whichever side they first appear on puts them in the wrong team half the
        time; placing them by their teammates does not.
        """
        m = match_with([
            sides_frame(0, ["a", "b"], ["c", "d"]),
            # second half: a and b are now CT, and `sub` joins alongside them
            frame(50000, fplayer("c", side="t"), fplayer("d", side="t"),
                  fplayer("a", side="ct"), fplayer("b", side="ct"),
                  fplayer("sub", side="ct"), rnd=13),
        ])
        t = roles.teams(m)
        assert t["sub"] == t["a"], "the substitute was placed by side, not team"

    def test_it_reads_the_side_split_from_a_frame_that_has_everyone(self):
        """
        Round one is the only frame guaranteed to hold all ten players. Read the
        split from the LAST frame instead and anyone dead or disconnected at
        match end has no starting side to read, so they fall through to the
        overlap pass - or, if their team is gone too, to nothing at all.

        The partition test above cannot see this: team ids are arbitrary, so
        reading a second-half frame produces the same two groups with the labels
        swapped and the assertion still passes.
        """
        m = match_with([
            sides_frame(0, ["a", "b"], ["c", "d"]),
            frame(50000, fplayer("c", side="t"), fplayer("d", side="t"),
                  rnd=13),          # a and b are dead in the final sample
        ])
        t = roles.teams(m)
        assert set(t) == {"a", "b", "c", "d"}, f"players lost their team: {t}"

    def test_no_frames_is_not_a_crash(self):
        assert roles.teams(make_match([], [])) == {}


class TestEvidence:
    def test_counts_carry_their_denominator(self, real_match):
        ev = roles.evidence(real_match)
        me = ev[real_match["pov"]]
        assert me["opening_duels_in_match"] > 0
        assert me["rounds"] > 0

    def test_the_utility_average_is_the_players_own_team(self, real_match):
        """
        Not the lobby. On the bundled match the lobby average is 35 and the two
        teams sit at 32 and 39 - the difference between suggesting the POV is
        the support and correctly staying quiet.
        """
        ev = roles.evidence(real_match)
        averages = {e["team_average_nades"] for e in ev.values()}
        assert len(averages) == 2, \
            f"expected one average per team, got {averages}"

    def test_teammates_are_listed_and_exclude_the_player(self, real_match):
        ev = roles.evidence(real_match)
        for name, e in ev.items():
            assert name not in e["teammates"]
            assert len(e["teammates"]) == 4

    def test_an_awp_in_the_loadout_is_counted(self):
        m = make_match([], [frame(0, fplayer("a"), rnd=1)])
        m["item_names"] = ["AK-47", "AWP"]
        m["loadouts"] = [[0], [1]]
        m["frames"] = [
            {"tick": 0, "round": 1, "players": [dict(fplayer("a"), eq=1)]},
            {"tick": 32, "round": 2, "players": [dict(fplayer("a"), eq=0)]},
        ]
        m["players"] = [{"name": "a", "steamid": "s"}]
        assert roles.evidence(m)["a"]["awp_rounds"] == 1

    def test_a_match_with_no_awp_in_it_does_not_crash(self, real_match):
        m = dict(real_match)
        m["item_names"] = [n for n in real_match["item_names"] if n != "AWP"]
        assert roles.evidence(m)  # no exception, no AWP rounds
        assert all(e["awp_rounds"] == 0 for e in roles.evidence(m).values())

    def test_only_the_first_kill_of_a_round_is_an_opening_duel(self):
        frames_ = [sides_frame(0, ["a", "b"], ["c", "d"])]
        kills = [kill(100, 1, "a", "c"), kill(200, 1, "a", "d"),
                 kill(300, 2, "b", "c")]
        m = match_with(frames_, kills, names=["a", "b", "c", "d"])
        ev = roles.evidence(m)
        assert ev["a"]["opening_kills"] == 1, "a later kill was counted as an opening"
        assert ev["b"]["opening_kills"] == 1
        assert ev["d"]["opening_deaths"] == 0


class TestSuggestRefuses:
    def test_a_short_sample_gets_no_suggestions(self, real_match):
        """
        The fixture is five rounds. Every threshold below the rounds floor is a
        ratio, and ratios are cheapest to satisfy on the smallest sample - one
        eco where a single player bought the grenades is 2x the team average.
        Before the floor existed this named the POV as the support off five
        rounds, which is the horoscope this module is written to avoid.
        """
        ev = roles.evidence(real_match)
        assert real_match["stats"]["rounds_played"] < roles.MIN_ROUNDS_FOR_SUGGESTION
        named = {n: roles.suggest(e) for n, e in ev.items() if roles.suggest(e)}
        assert not named, named

    def test_a_full_pug_spread_gets_no_suggestions(self):
        """
        22 rounds, evidence spread the way a real pug spreads it: the busiest
        player has five of the 22 opening duels and 1.5x the team's grenades.
        Both are the kind of number that looks like a role and is not one.
        """
        spread = [(5, 1, 60), (4, 1, 31), (2, 5, 24), (2, 3, 27), (1, 1, 45)]
        for won, lost, nades in spread:
            ev = {"rounds": 22, "awp_rounds": 0, "opening_kills": won,
                  "opening_deaths": lost, "opening_duels_in_match": 22,
                  "nades": nades, "team_average_nades": 39.0}
            assert roles.suggest(ev) is None, f"{won}W/{lost}L {nades} nades"

    def test_an_awp_in_four_rounds_is_not_an_awper(self):
        ev = {"rounds": 22, "awp_rounds": 4, "opening_kills": 2,
              "opening_deaths": 3, "opening_duels_in_match": 22,
              "nades": 48, "team_average_nades": 39.0}
        assert roles.suggest(ev) is None

    def test_an_awp_in_most_rounds_is_an_awper(self):
        """The refusal has to be about the evidence, not a hard-coded None."""
        ev = {"rounds": 22, "awp_rounds": 16, "opening_kills": 2,
              "opening_deaths": 3, "opening_duels_in_match": 22,
              "nades": 20, "team_average_nades": 39.0}
        assert roles.suggest(ev) == "awper"

    def test_a_dominant_share_of_opening_duels_is_an_entry(self):
        ev = {"rounds": 22, "awp_rounds": 0, "opening_kills": 6,
              "opening_deaths": 5, "opening_duels_in_match": 22,
              "nades": 20, "team_average_nades": 39.0}
        assert roles.suggest(ev) == "entry"

    def test_a_high_share_of_too_few_duels_is_still_refused(self):
        """
        Five of thirteen duels is 38% - past the share bar - and five duels is
        still five duels. The match is long enough to clear the rounds floor, so
        this reaches the duel-count floor specifically; a version keyed only on
        share lets it through.
        """
        ev = {"rounds": 13, "awp_rounds": 0, "opening_kills": 3,
              "opening_deaths": 2, "opening_duels_in_match": 13,
              "nades": 5, "team_average_nades": 5.0}
        assert ev["rounds"] >= roles.MIN_ROUNDS_FOR_SUGGESTION, \
            "test setup no longer reaches the duel-count floor"
        share = (ev["opening_kills"] + ev["opening_deaths"]) / ev["opening_duels_in_match"]
        assert share >= roles.MIN_OPENING_SHARE, "nor the share bar"
        assert roles.suggest(ev) is None

    def test_the_igl_is_never_suggested_for_any_evidence(self):
        """
        Nothing in a demo distinguishes the caller - it is in the comms, and
        even with voice extracted, talk time is not leadership.

        Swept across the whole space rather than one extreme case: a single
        maximal example returns from the AWP branch before it ever reaches the
        rest of the function, so an igl rule added further down goes unnoticed.
        """
        for rounds in (12, 22, 30, 40):     # 40: overtime, where the entry
            for awp in (0, 5, rounds):      # share bar stops catching things
                for won in (0, 3, 11):
                    for lost in (0, 2, 9):
                        for nades in (0, 40, 200):
                            ev = {"rounds": rounds, "awp_rounds": awp,
                                  "opening_kills": won, "opening_deaths": lost,
                                  "opening_duels_in_match": rounds,
                                  "nades": nades, "team_average_nades": 30.0}
                            assert roles.suggest(ev) != "igl", ev


class TestGuidance:
    def test_it_is_case_and_whitespace_insensitive(self):
        assert roles.guidance("  AWPer ") == roles.guidance("awper")

    def test_an_unknown_role_is_none_not_an_error(self):
        assert roles.guidance("rocket-league") is None
        assert roles.guidance(None) is None
        assert roles.guidance("") is None

    def test_every_role_in_ROLES_has_guidance(self):
        for r in roles.ROLES:
            assert roles.guidance(r), f"{r} is offered but has no rules"

    def test_the_fact_sheet_puts_the_role_after_the_numbers(self, analyze_mod,
                                                            real_match):
        import coach
        a = analyze_mod.analyze(real_match)
        sheet = coach.build_facts(a, real_match, role="entry")
        assert "THIS PLAYER'S ROLE: ENTRY" in sheet
        assert sheet.index("TOTALS") < sheet.index("THIS PLAYER'S ROLE")

    def test_no_role_leaves_the_sheet_alone(self, analyze_mod, real_match):
        import coach
        a = analyze_mod.analyze(real_match)
        assert "THIS PLAYER'S ROLE" not in coach.build_facts(a, real_match)
