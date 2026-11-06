"""
Tests for listing parsed matches, and for fixing a match parsed without a POV.

Both exist because of the same complaint - "the weapon icons don't load" - which
turned out to be two separate things, neither of them a loading failure:

  * opening the viewer with no ?match= fell straight through to the 1-round
    synthetic sample. It has no loadouts, so no weapon icons; no analysis, so no
    coach panel; no teams. The page looked broken while two real parsed matches
    sat in data/ with no way to discover them.

  * a demo uploaded without --pov gets reviewed as players[0], the first name
    alphabetically. The output is complete, internally consistent, and about a
    stranger - and nothing anywhere says so.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "parser"))

import app  # noqa: E402


def write_match(data, stem, rounds=2, map_name="de_test", pov=None,
                analysed=False):
    (data / f"{stem}.json").write_text(json.dumps({
        "map": map_name, "pov": pov,
        "rounds": [{"round_num": i + 1} for i in range(rounds)],
        "players": [{"name": "a", "steamid": "1"}],
    }), encoding="utf-8")
    if analysed:
        (data / f"{stem}_analysis.json").write_text(
            json.dumps({"player": "a", "deaths": []}), encoding="utf-8")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr(app, "ROOT", tmp_path)
    app._MATCH_META.clear()
    return d


class TestListMatches:
    def test_it_finds_parsed_matches(self, data_dir):
        write_match(data_dir, "alpha", rounds=22, map_name="de_anubis")
        names = [m["name"] for m in app._list_matches()]
        assert names == ["alpha"]

    def test_sidecars_are_not_listed_as_matches(self, data_dir):
        """
        An _analysis.json has no `rounds`, so it would list as an empty match
        and offer the user a review of nothing.
        """
        write_match(data_dir, "alpha")
        # These have to look like REAL sidecars, i.e. carry `rounds`, or the
        # "no rounds means not a match" check filters them on its own and the
        # suffix rule is never exercised. _sample_mirage in particular is a
        # genuine one-round match and is excluded only by the leading
        # underscore - which is the whole bug this file is about.
        for sfx in ("_analysis", "_voice", "_coach", "_notes"):
            write_match(data_dir, f"alpha{sfx}")
        write_match(data_dir, "career")
        write_match(data_dir, "_sample_mirage", rounds=1)
        assert [m["name"] for m in app._list_matches()] == ["alpha"]

    def test_newest_first(self, data_dir):
        """The match you just uploaded is the one you want to look at."""
        import os
        import time
        # Names chosen so alphabetical order is the OPPOSITE of chronological.
        # With "old"/"new" both orderings give the same answer and a build
        # sorted by name passes - which is what the first version of this did.
        write_match(data_dir, "aaa_oldest")
        write_match(data_dir, "zzz_newest")
        now = time.time()
        os.utime(data_dir / "aaa_oldest.json", (now - 500, now - 500))
        os.utime(data_dir / "zzz_newest.json", (now, now))
        assert [m["name"] for m in app._list_matches()] ==             ["zzz_newest", "aaa_oldest"]

    def test_it_reports_whether_a_match_has_been_analysed(self, data_dir):
        write_match(data_dir, "raw", analysed=False)
        write_match(data_dir, "done", analysed=True)
        got = {m["name"]: m["analysed"] for m in app._list_matches()}
        assert got == {"raw": False, "done": True}

    def test_a_corrupt_match_is_skipped_not_fatal(self, data_dir):
        write_match(data_dir, "good")
        (data_dir / "broken.json").write_text("{not json", encoding="utf-8")
        assert [m["name"] for m in app._list_matches()] == ["good"]

    def test_no_data_directory_is_empty_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(app, "ROOT", tmp_path / "nowhere")
        assert app._list_matches() == []


class TestListingIsCached:
    """
    A match JSON is ~5 MB and the listing needs four fields. Measured, parsing
    two took 154 ms, so twenty would have put 1.5s of JSON parsing in front of
    every page load - for a dropdown.
    """

    def test_a_second_listing_does_not_reread_the_file(self, data_dir,
                                                        monkeypatch):
        write_match(data_dir, "alpha")
        app._list_matches()

        reads = []
        real = Path.read_text

        def counting(self, *a, **kw):
            if self.suffix == ".json" and not self.stem.endswith("_analysis"):
                reads.append(self.name)
            return real(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", counting)
        app._list_matches()
        assert reads == [], f"re-read {reads} when nothing had changed"

    def test_a_reparsed_match_is_picked_up(self, data_dir):
        """
        Keyed on mtime, so re-parsing a demo must change what the list says
        rather than serving the previous parse forever.
        """
        import os
        import time
        write_match(data_dir, "alpha", rounds=2)
        assert app._list_matches()[0]["rounds"] == 2

        write_match(data_dir, "alpha", rounds=30)
        later = time.time() + 10
        os.utime(data_dir / "alpha.json", (later, later))
        assert app._list_matches()[0]["rounds"] == 30

    def test_the_analysed_flag_is_not_cached(self, data_dir):
        """
        Picking a POV analyses the match. A cached "not analysed" would
        survive that and keep telling the user it never happened.
        """
        write_match(data_dir, "alpha", analysed=False)
        assert app._list_matches()[0]["analysed"] is False
        (data_dir / "alpha_analysis.json").write_text('{"deaths":[]}',
                                                      encoding="utf-8")
        assert app._list_matches()[0]["analysed"] is True


class TestPovIsNotGuessed:
    def test_analyze_falls_back_to_the_first_player_when_given_none(self):
        """
        Documents the behaviour the POV picker exists to correct. This is not
        a bug in analyze - it has to pick something - but it is silent, and
        silence is what makes it dangerous.
        """
        import analyze
        match = {
            "map": "de_test", "pov": None, "tickrate": 64,
            "players": [{"name": "aaa", "steamid": "1"},
                        {"name": "zzz", "steamid": "2"}],
            "rounds": [{"round_num": 1, "start": 0, "freeze_end": 10,
                        "end": 100, "official_end": 100}],
            "frames": [], "kills": [], "damages": [], "utility": {},
        }
        assert analyze.analyze(match)["player"] == "aaa"

    def test_a_player_not_in_the_match_is_refused(self):
        """
        analyze() resolves an unknown player back to players[0], so an
        unchecked typo does not error - it silently re-reviews the stranger
        this whole feature exists to stop reviewing.

        Tested against the function, not the source. The source-inspection
        version of this passed a build with the check deleted, because the
        error string was still sitting there unreachable.
        """
        import upload as up
        match = {"players": [{"name": "aaa"}, {"name": "zzz"}]}
        assert up.player_in(match, "zzz") is True
        assert up.player_in(match, "nobody") is False
        assert up.player_in(match, "") is False
        assert up.player_in(match, None) is False
        assert up.player_in({}, "aaa") is False

        # The empty/None guard, which the list membership test alone does not
        # reach: parse_demo writes `name: row.get(...)`, so a demo missing the
        # name column yields player entries with a null name. Without the
        # guard, `None in [None]` is True and "nobody" becomes a valid POV.
        nameless = {"players": [{"name": None}, {"steamid": "1"}]}
        assert up.player_in(nameless, None) is False
        assert up.player_in(nameless, "") is False

    def test_the_handler_uses_that_check(self):
        src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        body = src[src.index("def _set_pov"):src.index("def _upload")]
        assert "up.player_in(" in body

    def test_changing_the_pov_clears_every_cache(self):
        """
        The server holds the match, the chat memoises a brief per player, and
        the career is pooled per player. Miss any one and the review updates
        while the coach carries on answering about the previous person.
        """
        src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        forget = src[src.index("def forget_match"):src.index("def load_match")]
        assert "_MATCH_CACHE.pop" in forget
        assert "_brief_cache.clear" in forget
        assert "_career_cache.clear" in forget

        body = src[src.index("def _set_pov"):src.index("def _upload")]
        assert "forget_match(" in body
