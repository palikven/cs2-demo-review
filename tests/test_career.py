"""
Tests for pooling a player across matches.

The whole value of this module is that a bigger sample clears bars one match
cannot. That makes the arithmetic load-bearing in a way per-match stats are not,
and there is one specific way to get it wrong that looks right:

    match A: died once, holding utility        -> 100%
    match B: died twenty times, holding it ten ->  50%
    average of the rates                       ->  75%
    actual                                     ->  52%

Averaging per-match percentages weights a one-death match the same as a
twenty-death one. Every rate here must come from summed numerators over summed
denominators, and the test below is built on exactly that asymmetry so a build
that averages cannot pass it.

The other half is verified against real data: the bundled match split into two
halves must pool back to the numbers the whole match produces. That exercises
the real analysis pipeline end to end, which no synthetic fixture does.
"""

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import career  # noqa: E402


def fake_analysis(deaths_at=(), kit=None, flashes=None, comms=None, aim=None):
    a = {"deaths": [{"victim_place": p, "tick": i}
                    for i, p in enumerate(deaths_at)]}
    if kit:
        a["kit"] = kit
    if flashes:
        a["flashes"] = flashes
    if comms:
        a["comms"] = comms
    if aim:
        a["aim"] = aim
    return a


class TestPoolingNotAveraging:
    def test_rates_pool_the_counts(self):
        """
        The asymmetry is the point: 1 death and 20 deaths. Averaging gives 75%,
        pooling gives 52%, and only one of those is the player's actual rate.
        """
        rows = [
            ("a", fake_analysis(kit={"deaths_holding_utility": 1, "deaths": 1})),
            ("b", fake_analysis(kit={"deaths_holding_utility": 10, "deaths": 20})),
        ]
        r = career.career(rows, "76561198000000000")["rates"]["died_holding_utility"]
        assert (r["n"], r["of"]) == (11, 21)
        assert r["rate"] == pytest.approx(11 / 21, abs=0.001)
        assert r["rate"] != pytest.approx(0.75, abs=0.01), \
            "per-match percentages are being averaged"

    def test_the_per_match_series_is_kept_but_not_used_for_the_total(self):
        rows = [
            ("a", fake_analysis(kit={"deaths_holding_utility": 1, "deaths": 1})),
            ("b", fake_analysis(kit={"deaths_holding_utility": 10, "deaths": 20})),
        ]
        r = career.career(rows, "x")["rates"]["died_holding_utility"]
        assert r["per_match"] == [1.0, 0.5]
        assert r["rate"] < min(r["per_match"]) or r["rate"] > max(r["per_match"]) \
            or r["rate"] == pytest.approx(11 / 21, abs=0.001)

    def test_a_match_where_they_never_died_does_not_divide_by_zero(self):
        rows = [("a", fake_analysis(kit={"deaths_holding_utility": 0, "deaths": 0})),
                ("b", fake_analysis(kit={"deaths_holding_utility": 3, "deaths": 6}))]
        r = career.career(rows, "x")["rates"]["died_holding_utility"]
        assert r["rate"] == pytest.approx(0.5)
        assert r["per_match"][0] is None


class TestTrend:
    def test_two_matches_are_not_a_trend(self):
        """Two points and a straight line through them is not a direction."""
        assert career._trend([0.1, 0.9]) is None

    def test_enough_matches_give_a_direction(self):
        t = career._trend([0.1, 0.1, 0.9, 0.9])
        assert t["delta"] == pytest.approx(0.8, abs=0.01)
        assert t["matches"] == 4

    def test_missing_matches_are_skipped_not_counted_as_zero(self):
        """A match with no comms extracted has not got a 0% call rate."""
        assert career._trend([0.5, None, 0.5]) is None
        t = career._trend([0.5, None, 0.5, 0.5, 0.5])
        assert t["matches"] == 4


class TestHotspots:
    def test_a_habit_needs_to_span_matches_not_just_one_bad_night(self):
        """
        Twelve deaths in Main across one of four matches is one bad night that
        happens to dominate the pool. The count bar alone passes it.
        """
        rows = [("m1", fake_analysis(deaths_at=["Main"] * 12))]
        rows += [(f"m{i}", fake_analysis(deaths_at=["Palace"] * 4))
                 for i in range(2, 5)]
        c = career.career(rows, "x")
        main = next(h for h in c["hotspots"] if h["place"] == "Main")
        assert main["deaths"] == 12
        assert main["matches"] == 1
        assert main["notable"] is False, "one match cannot make a habit"

    def test_a_spot_that_recurs_across_matches_is_notable(self):
        rows = [(f"m{i}", fake_analysis(deaths_at=["Main"] * 3 + ["X"]))
                for i in range(4)]
        c = career.career(rows, "x")
        main = next(h for h in c["hotspots"] if h["place"] == "Main")
        assert main["matches"] == 4
        assert main["notable"] is True

    def test_counts_are_summed_across_matches(self):
        rows = [(f"m{i}", fake_analysis(deaths_at=["Main", "Main"]))
                for i in range(5)]
        c = career.career(rows, "x")
        assert c["deaths"] == 10
        assert c["hotspots"][0]["deaths"] == 10


class TestIdentity:
    def test_a_player_is_found_by_steamid_not_name(self):
        """People rename constantly; the steamid does not."""
        match = {"players": [{"name": "NewName", "steamid": "7656119900"}]}
        assert career.identify(match, steamid="7656119900") == \
            ("7656119900", "NewName")

    def test_name_still_works_when_no_steamid_is_given(self):
        match = {"players": [{"name": "pav", "steamid": "765"}]}
        assert career.identify(match, name="pav") == ("765", "pav")

    def test_a_player_not_in_the_match_is_none(self):
        match = {"players": [{"name": "a", "steamid": "1"}]}
        assert career.identify(match, steamid="2") is None


class TestFindMatches:
    def test_sidecars_are_not_mistaken_for_matches(self, tmp_path):
        """
        An _analysis.json has a `deaths` key and would read as a match with no
        rounds - silently doubling every count.
        """
        for name in ("m1.json", "m1_analysis.json", "m1_voice.json",
                     "m1_coach.json", "_sample.json"):
            (tmp_path / name).write_text("{}", encoding="utf-8")
        found = [p.name for p in career.find_matches(tmp_path)]
        assert found == ["m1.json"]


class TestAgainstRealData:
    """
    The bundled match cut in half, pooled, and compared to the whole.

    This runs the real pipeline - analyze, flashes, kit, comms - rather than
    hand-built dicts, so it catches a pooling bug that a synthetic fixture would
    agree with.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def split(cls):
        import analyze
        path = ROOT / "data" / "2v5.json"
        if not path.exists():
            pytest.skip("no parsed match on this machine")
        m = analyze.load_match(path)

        def cut(lo, hi):
            s = copy.deepcopy(m)
            s["rounds"] = [r for r in m["rounds"] if lo <= r["round_num"] <= hi]
            t0 = min(r["start"] for r in s["rounds"])
            t1 = max(r.get("official_end") or r["end"] for r in s["rounds"])
            s["frames"] = [f for f in m["frames"] if t0 <= f["tick"] <= t1]
            s["kills"] = [k for k in m["kills"] if lo <= (k.get("round") or 0) <= hi]
            s["damages"] = [d for d in m["damages"] if lo <= (d.get("r") or 0) <= hi]
            u = dict(m["utility"])
            u["blinds"] = [b for b in (m["utility"].get("blinds") or [])
                           if lo <= (b.get("r") or 0) <= hi]
            s["utility"] = u
            if m.get("_voice"):
                s["_voice"] = {
                    "tickrate": m["_voice"]["tickrate"],
                    "players": [{"name": p["name"],
                                 "utterances": [x for x in p["utterances"]
                                                if t0 <= x["s"] <= t1]}
                                for p in m["_voice"]["players"]]}
            return s

        pov = m["pov"]
        whole = analyze.analyze(m, pov)
        whole.setdefault("flashes", {})["_flash_total"] = career._flash_total(whole)
        halves = []
        for lo, hi in ((1, 12), (13, 22)):
            a = analyze.analyze(cut(lo, hi), pov)
            if a.get("flashes"):
                a["flashes"]["_flash_total"] = career._flash_total(a)
            halves.append((f"r{lo}-{hi}", a))
        return whole, career.career(halves, "x")

    def test_deaths_reconstruct(self, split):
        whole, pooled = split
        assert pooled["deaths"] == len(whole["deaths"])

    @pytest.mark.parametrize("key,source,num,den", [
        ("team_flash", "flashes", "team_meaningful", "_flash_total"),
        ("died_holding_utility", "kit", "deaths_holding_utility", "deaths"),
        ("mic_open_after_death", "comms", "called_after",
         "deaths_with_time_to_call"),
    ])
    def test_each_rate_reconstructs(self, split, key, source, num, den):
        whole, pooled = split
        w = whole.get(source) or {}
        if w.get(den) is None:
            pytest.skip(f"{source} not present in this match")
        r = pooled["rates"].get(key)
        assert r, f"{key} missing from the pooled career"
        assert (r["n"], r["of"]) == (w[num], w[den])

    def test_the_hotspot_counts_reconstruct(self, split):
        whole, pooled = split
        from collections import Counter
        want = Counter(d["victim_place"] for d in whole["deaths"]
                       if d.get("victim_place"))
        got = {h["place"]: h["deaths"] for h in pooled["hotspots"]}
        assert got == dict(want)


class TestHonesty:
    def test_the_note_says_rates_are_pooled(self):
        c = career.career([("a", fake_analysis(deaths_at=["Main"]))], "x")
        assert "pooled numerator over pooled denominator" in c["note"]

    def test_the_note_warns_that_ordering_is_parse_time(self):
        c = career.career([("a", fake_analysis(deaths_at=["Main"]))], "x")
        assert "not when it was" in c["note"]


class TestSidecar:
    """
    career.json exists for a measured reason: analysing one match costs about a
    second, so a player with twenty parsed demos would wait twenty seconds
    looking at an empty chat before the first token. The chat must READ this
    file and never rebuild it.
    """

    def test_emit_then_load_round_trips(self, tmp_path):
        c = career.career([("a", fake_analysis(deaths_at=["Main"]))],
                          "76561198000000001")
        career.emit(tmp_path, c)
        assert career.load(tmp_path, "76561198000000001")["deaths"] == 1

    def test_a_second_player_does_not_overwrite_the_first(self, tmp_path):
        career.emit(tmp_path, career.career(
            [("a", fake_analysis(deaths_at=["Main"]))], "111"))
        career.emit(tmp_path, career.career(
            [("a", fake_analysis(deaths_at=["Palace", "Palace"]))], "222"))
        assert career.load(tmp_path, "111")["deaths"] == 1
        assert career.load(tmp_path, "222")["deaths"] == 2

    def test_a_missing_file_is_empty_not_an_error(self, tmp_path):
        assert career.load(tmp_path, "111") == {}

    def test_a_corrupt_sidecar_is_empty_not_an_error(self, tmp_path):
        """A half-written file must not take the chat down with it."""
        (tmp_path / career.CAREER_FILE).write_text("{not json", encoding="utf-8")
        assert career.load(tmp_path, "111") == {}
        # and it must still be writable afterwards
        career.emit(tmp_path, career.career(
            [("a", fake_analysis(deaths_at=["Main"]))], "111"))
        assert career.load(tmp_path, "111")["deaths"] == 1

    def test_it_records_what_it_was_built_from(self, tmp_path):
        """
        The file goes stale the moment a new demo is parsed. Recording the
        source matches is what lets a reader notice.
        """
        c = career.career([("m1", fake_analysis(deaths_at=["Main"])),
                           ("m2", fake_analysis(deaths_at=["Main"]))], "111")
        career.emit(tmp_path, c)
        assert career.load(tmp_path, "111")["generated_matches"] == ["m1", "m2"]


class TestChatIntegration:
    def test_the_chat_reads_the_sidecar_and_never_rebuilds(self):
        """
        Rebuilding here is the twenty-second stall. Pinned as source inspection
        because the alternative is a test that takes twenty seconds to prove it.
        """
        src = (ROOT / "server" / "chat.py").read_text(encoding="utf-8")
        assert "careermod.load(" in src
        assert "careermod.build(" not in src, \
            "the chat rebuilds the career - that is the stall this avoids"

    def test_one_match_produces_no_career_section(self):
        """
        A single match pooled is that match with a bigger label on it. Saying
        'across 1 matches' invites the model to treat one night as a pattern.
        """
        import coach
        c = career.career([("a", fake_analysis(deaths_at=["Main"] * 5))], "111")
        assert c["matches"] == 1
        assert coach.career_lines(c) == []

    def test_two_matches_do_produce_one(self):
        import coach
        c = career.career([("a", fake_analysis(deaths_at=["Main"] * 5)),
                           ("b", fake_analysis(deaths_at=["Main"] * 5))], "111")
        lines = coach.career_lines(c)
        assert lines and "ACROSS 2 PARSED MATCHES" in lines[1]
        assert "NOT this match" in lines[1]

    def test_it_refuses_to_name_a_spot_that_has_not_earned_it(self):
        """Nothing notable must produce an explicit 'do not name one'."""
        import coach
        rows = [(f"m{i}", fake_analysis(deaths_at=[f"Place{i}"]))
                for i in range(4)]
        text = "\n".join(coach.career_lines(career.career(rows, "111")))
        assert "Do NOT name one anyway" in text
