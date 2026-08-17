"""
Tests for parser/coach.py.

The API call itself isn't tested - it's one SDK call and mocking it would only
assert that the mock was written correctly. What IS tested is everything that
decides whether the coaching is true:

  * the fact sheet, because a wrong sheet means wrong advice no matter how
    good the model is;
  * the guard rails that tell the model what it may NOT claim;
  * the (round, tick) validation, because a hallucinated tick puts real-looking
    advice at a moment of the match that never happened.
"""

import json
import sys
from pathlib import Path

import pytest

from conftest import fplayer, frame, kill, make_match

PARSER_DIR = Path(__file__).resolve().parent.parent / "parser"
sys.path.insert(0, str(PARSER_DIR))


@pytest.fixture(scope="session")
def coach_mod():
    import coach

    return coach


@pytest.fixture
def analysed(analyze_mod, real_match):
    return analyze_mod.analyze(real_match)


# ---------------------------------------------------------------------------
class TestFactSheet:
    def test_every_death_appears_with_its_tick(self, coach_mod, analysed, real_match):
        facts = coach_mod.build_facts(analysed, real_match)
        for d in analysed["deaths"]:
            assert f"tick {d['tick']}" in facts

    def test_both_callouts_are_named(self, coach_mod, analysed, real_match):
        facts = coach_mod.build_facts(analysed, real_match)
        for d in analysed["deaths"]:
            assert f"died in {d['victim_place']}" in facts
            assert f"killed from {d['attacker_place']}" in facts

    def test_untraded_is_stated_loudly_not_by_omission(self, coach_mod, analysed,
                                                       real_match):
        """
        A model reading "traded: false" and a model reading nothing behave the
        same way - they ignore it. The sheet has to assert the negative.
        """
        facts = coach_mod.build_facts(analysed, real_match)
        assert "NOT traded" in facts

    def test_last_man_alive_is_said_explicitly(self, coach_mod, analysed, real_match):
        """Otherwise 'nobody traded you' reads as a teammate's failure."""
        facts = coach_mod.build_facts(analysed, real_match)
        assert "last player alive on the team" in facts

    def test_no_coordinates_leak_into_the_sheet(self, coach_mod, analysed, real_match):
        """
        The project's one hard rule: the model sees names and counts, never
        positions. Distances are fine - they're a measurement, not a location.
        """
        facts = coach_mod.build_facts(analysed, real_match)
        for d in analysed["deaths"]:
            for key in ("vx", "vy", "ax", "ay"):
                raw = next((k.get(key) for k in real_match["kills"]
                            if k.get("tick") == d["tick"]), None)
                if raw is not None:
                    assert str(raw) not in facts


class TestGuardRails:
    def _match_with_deaths(self, places, mate_dist_death, mate_dist_kill):
        kills, frames_ = [], []
        tick = 1000
        for i, place in enumerate(places):
            frames_.append(frame(tick - 32,
                                 fplayer("me", 0, 0, "t"),
                                 fplayer("mate", mate_dist_death, 0, "t"),
                                 fplayer("e1", 5000, 0, "ct")))
            kills.append(kill(tick, i + 1, "e1", "me", aside="ct", vside="t",
                              vplace=place, aplace="Connector", vx=0.0, vy=0.0))
            # a kill by "me", to give the spacing comparison something to chew on
            frames_.append(frame(tick + 64 - 32,
                                 fplayer("me", 0, 0, "t"),
                                 fplayer("mate", mate_dist_kill, 0, "t"),
                                 fplayer("e2", 5000, 0, "ct")))
            kills.append(kill(tick + 64, i + 1, "me", "e2", aside="t", vside="ct",
                              ax=0.0, ay=0.0))
            tick += 10000
        return make_match(kills, frames_, pov="me")

    def test_repeated_spot_is_marked_notable(self, coach_mod, analyze_mod):
        m = self._match_with_deaths(["Main"] * 4, 300, 300)
        facts = coach_mod.build_facts(analyze_mod.analyze(m), m)
        assert "[NOTABLE" in facts

    def test_a_single_death_per_spot_claims_no_habit(self, coach_mod, analyze_mod):
        """One death in a spot is where you happened to die, not a habit."""
        m = self._match_with_deaths(["Main", "Palace", "Canal", "Water"], 300, 300)
        facts = coach_mod.build_facts(analyze_mod.analyze(m), m)
        assert "Do not claim a positional habit" in facts

    def test_sheet_forbids_the_spacing_story_when_the_numbers_deny_it(
            self, coach_mod, analyze_mod):
        """
        Closer to teammates when dying than when winning duels means spacing is
        not this player's problem - and left unsaid, "isolation" is exactly the
        plausible-sounding story a model reaches for.
        """
        m = self._match_with_deaths(["Main"] * 3, 200, 900)
        facts = coach_mod.build_facts(analyze_mod.analyze(m), m)
        assert "Spacing is not the problem in this match" in facts

    def test_no_such_note_when_the_player_really_is_isolated(self, coach_mod,
                                                             analyze_mod):
        m = self._match_with_deaths(["Main"] * 3, 900, 200)
        facts = coach_mod.build_facts(analyze_mod.analyze(m), m)
        assert "Spacing is not the problem" not in facts

    def test_real_match_sheet_carries_its_own_correction(self, coach_mod, analysed,
                                                          real_match):
        """pavloven is closer to support when dying - the sheet must say so."""
        iso = analysed["isolation_profile"]
        facts = coach_mod.build_facts(analysed, real_match)
        if iso["median_dist_at_death"] <= iso["median_dist_at_kill"]:
            assert "Spacing is not the problem" in facts


# ---------------------------------------------------------------------------
class _StubResponse:
    def __init__(self, parsed):
        self.parsed_output = parsed
        self.usage = None


class _StubClient:
    """Returns whatever coaching we hand it, recording the request."""

    def __init__(self, parsed):
        self._parsed = parsed
        self.seen = {}
        self.messages = self

    def parse(self, **kwargs):
        self.seen = kwargs
        return _StubResponse(self._parsed)


class TestHallucinationGuard:
    def _coaching(self, coach_mod, pairs):
        return coach_mod.MatchCoaching(
            summary="s",
            patterns=[],
            deaths=[coach_mod.DeathTip(round=r, tick=t, verdict="v", tip="t")
                    for r, t in pairs],
            rounds=[],
        )

    def test_keeps_notes_anchored_to_real_deaths(self, coach_mod, analysed, real_match):
        real = [(d["round"], d["tick"]) for d in analysed["deaths"]][:2]
        client = _StubClient(self._coaching(coach_mod, real))
        out = coach_mod.coach(analysed, real_match, client=client)
        assert [(d.round, d.tick) for d in out.deaths] == real

    def test_drops_notes_pointing_at_ticks_that_never_happened(
            self, coach_mod, analysed, real_match, capsys):
        real = [(d["round"], d["tick"]) for d in analysed["deaths"]][:1]
        client = _StubClient(self._coaching(coach_mod, real + [(99, 123456)]))
        out = coach_mod.coach(analysed, real_match, client=client)
        assert [(d.round, d.tick) for d in out.deaths] == real
        assert "Dropped 1 death note" in capsys.readouterr().out

    def test_right_tick_but_wrong_round_is_still_dropped(self, coach_mod, analysed,
                                                          real_match):
        """The pair is the anchor; a tick alone isn't enough to trust."""
        r, t = analysed["deaths"][0]["round"], analysed["deaths"][0]["tick"]
        client = _StubClient(self._coaching(coach_mod, [(r + 1, t)]))
        out = coach_mod.coach(analysed, real_match, client=client)
        assert out.deaths == []


class TestRequestShape:
    @pytest.fixture
    def sent(self, coach_mod, analysed, real_match):
        client = _StubClient(coach_mod.MatchCoaching(
            summary="s", patterns=[], deaths=[], rounds=[]))
        coach_mod.coach(analysed, real_match, client=client)
        return client.seen

    def test_uses_the_current_opus_model(self, sent):
        assert sent["model"] == "claude-opus-5"

    def test_asks_for_the_structured_schema(self, coach_mod, sent):
        assert sent["output_format"] is coach_mod.MatchCoaching

    def test_system_prompt_is_cached(self, sent):
        """It's identical for every match; the fact sheet after it is not."""
        assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_the_volatile_facts_go_after_the_cache_breakpoint(self, sent):
        """A fact sheet inside the cached prefix would invalidate it per match."""
        assert "DEATHS" not in sent["system"][0]["text"]
        assert "DEATHS" in sent["messages"][0]["content"]

    def test_adaptive_thinking_is_on(self, sent):
        assert sent["thinking"] == {"type": "adaptive"}


class TestPayload:
    def test_shape_is_flat_and_json_serialisable(self, coach_mod, analysed):
        out = coach_mod.MatchCoaching(
            summary="ok",
            patterns=[coach_mod.Pattern(title="t", detail="d", tip="p")],
            deaths=[coach_mod.DeathTip(round=1, tick=2, verdict="v", tip="p")],
            rounds=[coach_mod.RoundTip(round=1, tip="p")],
        )
        payload = coach_mod.to_payload(out, analysed)
        json.dumps(payload)          # must not raise
        assert payload["player"] == analysed["player"]
        assert payload["deaths"][0]["tick"] == 2
        assert payload["model"] == "claude-opus-5"


# ---------------------------------------------------------------------------
class TestApproachInTheFactSheet:
    """
    How the duel was entered, written into the sheet.

    The risk here is not a missing number, it is a number stated more
    confidently than it was measured. The sample is a ~0.5s average ending up
    to 0.5s before the kill; a sheet that says "standing still" without saying
    when will be read as "standing still at the moment of death", and the
    advice that follows is about a moment nobody measured.
    """

    def test_a_death_carries_its_speed_and_who_closed(self, coach_mod):
        tags = coach_mod._approach_tags({
            "motion": "running", "speed": 186.0, "lag_s": 0.4,
            "motion_other": "static", "closed_by": "victim",
            "gap_start": 1029.0, "victim_closed": 314.0, "attacker_closed": 114.0,
        })
        joined = " ".join(tags)
        assert "186" in joined
        assert "closed the ground into this duel" in joined
        assert "+314" in joined and "1029" in joined

    def test_it_states_how_stale_the_sample_is(self, coach_mod):
        tags = coach_mod._approach_tags({"motion": "static", "speed": 12.0,
                                         "lag_s": 0.4})
        joined = " ".join(tags)
        assert "0.4s early" in joined, \
            "without the lag this reads as the instant of death"

    def test_an_unmeasured_duel_says_nothing(self, coach_mod):
        """Silence, not a default. 'Standing still' about an unmeasured duel is a lie."""
        assert coach_mod._approach_tags({"round": 3, "tick": 99}) == []

    def test_a_sightline_duel_is_named_as_one(self, coach_mod):
        tags = coach_mod._approach_tags({"closed_by": "neither",
                                         "gap_start": 900.0,
                                         "victim_closed": 3.0,
                                         "attacker_closed": -1.0})
        assert "already in" in " ".join(tags)

    def test_the_real_match_sheet_forbids_generalising(self, coach_mod, analysed,
                                                       real_match):
        """
        On one match the movement difference does not survive a permutation
        test, and the sheet has to say so in words the model cannot read past.
        If this ever flips to NOTABLE on the fixture, the gate was loosened.
        """
        sheet = coach_mod.build_facts(analysed, real_match)
        assert "HOW THIS PLAYER ENTERS DUELS" in sheet
        assert "[NOT A PATTERN]" in sheet
        assert "do NOT say" in sheet

    def test_the_aggregate_reports_the_lobby_baseline_too(self, coach_mod,
                                                          analysed, real_match):
        """A speed with nothing to compare it against is not a fact about anyone."""
        sheet = coach_mod.build_facts(analysed, real_match)
        line = next(l for l in sheet.splitlines()
                    if l.startswith("- Median speed when dying"))
        assert "Everyone else in this match" in line

    def test_a_claimable_profile_is_marked_notable(self, coach_mod, analysed):
        """
        The refusal has to be conditional. Swap in a profile that did clear the
        test and the sheet must say so - otherwise the gate is a hard-coded no.
        """
        a = dict(analysed)
        a["duel_profile"] = {
            "deaths_measured": 40, "kills_measured": 40,
            "speed_at_death": 190.0, "speed_at_kill": 30.0,
            "lobby_speed_at_death": 40.0, "p_vs_lobby": 0.001,
            "closed_into_death": 30, "approaches_measured": 40,
            "claimable": True, "note": "The speed difference is significant.",
        }
        sheet = coach_mod.build_facts(a, {})
        assert "[NOTABLE]" in sheet
        assert "[NOT A PATTERN]" not in sheet

    def test_no_profile_adds_no_section(self, coach_mod, analysed):
        a = dict(analysed)
        a["duel_profile"] = {}
        assert "HOW THIS PLAYER ENTERS DUELS" not in coach_mod.build_facts(a, {})
