import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))


class TestDeterminism:
    """
    Parsing the same demo twice must produce the same bytes.

    It did not. polars' .unique() is hash-based and parallel, so the player
    list came back in a different order on every run - three parses of one demo
    gave three different files with identical counts. Two things broke quietly:

      * the viewer picks players[0] when no --pov was given, so the default
        selected player changed between parses of the SAME demo
      * nothing downstream could use a content hash to tell whether a re-parse
        had actually changed anything, which is what makes caching and diffing
        worth doing at all

    Re-parsing a 354 MB demo takes ~10s, so this checks the ordering guarantee
    directly rather than by parsing twice.
    """

    def test_the_player_list_is_sorted(self):
        import json
        path = ROOT / "data" / "2v5.json"
        if not path.exists():
            pytest.skip("no parsed match on this machine")
        names = [p["name"] for p in json.loads(
            path.read_text(encoding="utf-8"))["players"]]
        assert names == sorted(names), \
            "player order is not stable - re-parsing gives a different file"

    def test_the_parser_sorts_rather_than_trusting_unique(self):
        """
        Pinned as source inspection: the bug is in a polars call whose output
        order is undefined, and an assertion on one parse cannot see that the
        NEXT parse would differ.
        """
        src = (ROOT / "parser" / "parse_demo.py").read_text(encoding="utf-8")
        i = src.index("players = (")
        block = src[i:i + 400]
        assert ".sort(" in block, \
            "the player list comes straight from .unique(), whose order is undefined"
