"""
Tests for the coach chat's context assembly.

What the model is told IS the product here. A chat that looks fine and quietly
answers from the wrong context is worse than one that errors, so these pin the
two things that decide answer quality:

  * the brief actually contains the numbers the coaching depends on - rating,
    KAST, swing, every death with its callouts
  * it contains NO live enemy positions

That second one is the invariant most at risk. The whole premise is that the
coach knows what the player knew; `snapshot()` in analyze.py deliberately never
constructs enemy records (see the comment at analyze.py:414). Widening context
is exactly the kind of "helpful" change that would break it silently, because
the answers would still read plausibly.

The provider call itself is not tested here - it needs a real key, and there
isn't one in this environment. Everything up to the SDK boundary is.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "parser"))

import chat as chatmod  # noqa: E402


@pytest.fixture(scope="session")
def brief(real_match):
    return chatmod.build_brief(real_match, "_fixture", real_match.get("pov"))


class TestBriefContents:
    """The coach can only cite what it was given."""

    def test_it_names_the_player_and_map(self, brief, real_match):
        assert real_match["pov"] in brief
        assert real_match["map"] in brief

    def test_it_carries_the_headline_numbers(self, brief):
        for token in ("Rating", "KAST", "ADR"):
            assert token in brief, f"{token} missing - the coach can't cite it"

    def test_it_carries_every_death_tick(self, brief, real_match):
        """
        The ticks are how a death gets anchored to the timeline. A brief that
        drops one means the coach cannot talk about that death at all.
        """
        import analyze
        a = analyze.analyze(real_match, real_match["pov"])
        assert a["deaths"], "fixture has no deaths to check"
        for d in a["deaths"]:
            assert str(d["tick"]) in brief, f"death at tick {d['tick']} not in brief"

    def test_it_names_the_callouts_of_each_death(self, brief, real_match):
        import analyze
        a = analyze.analyze(real_match, real_match["pov"])
        for d in a["deaths"]:
            if d.get("victim_place"):
                assert d["victim_place"] in brief

    def test_it_includes_the_team_scoreboard(self, brief, real_match):
        """Questions like 'am I the problem' need the other nine players."""
        assert "TEAM SCOREBOARD" in brief
        for name in (real_match.get("stats") or {}).get("per_player", {}):
            assert name in brief, f"{name} missing from the scoreboard"

    def test_it_includes_round_results(self, brief):
        assert "ROUND RESULTS" in brief

    def test_swing_is_labelled_as_ours_not_hltvs(self, brief):
        """
        The brief inherits build_facts()' disclaimer. If it ever stops saying
        so, the model will happily present our model's number as HLTV's.
        """
        if "swing" in brief.lower():
            assert "not HLTV" in brief or "OUR model" in brief


class TestEnemyBlindness:
    """
    The invariant that makes the coaching honest. If the model can see where
    the enemies were, every answer becomes hindsight dressed as insight.
    """

    def test_the_moment_states_the_limit_explicitly(self, real_match):
        tick = real_match["frames"][len(real_match["frames"]) // 2]["tick"]
        text = chatmod.moment(real_match, tick, real_match["pov"])
        assert "do not know where the enemies are" in text.lower()

    def test_no_enemy_is_named_as_a_position(self, real_match):
        """
        Every player named in the moment must be the player themselves or a
        teammate. An enemy name appearing next to a callout would mean live
        enemy positions leaked into the context.
        """
        tick = real_match["frames"][len(real_match["frames"]) // 2]["tick"]
        import analyze
        snap = analyze.snapshot(real_match, tick, real_match["pov"])
        if snap is None:
            pytest.skip("no snapshot at that tick")
        allowed = {snap["player"]} | {m["name"] for m in snap["teammates"]}
        text = chatmod.moment(real_match, tick, real_match["pov"])
        for name in (real_match.get("stats") or {}).get("per_player", {}):
            if name in allowed:
                continue
            # An enemy may legitimately appear in the kill log ("X died in Y"),
            # which is history the player watched happen - but never in the
            # live position list above it.
            live = text.split("This round so far:")[0]
            assert name not in live, f"enemy {name} leaked into live positions"

    def test_the_system_prompt_forbids_inventing_enemies(self):
        assert "do not know where the enemies" in chatmod.SYSTEM.lower()


class TestCacheStructure:
    """
    Caching is a prefix match: stable content first, volatile last. Get the
    order wrong and every follow-up turn re-bills ~4k tokens instead of reading
    them back at a tenth of the price.
    """

    def test_the_brief_is_byte_identical_across_calls(self, real_match):
        a = chatmod.build_brief(real_match, "_fixture", real_match["pov"])
        b = chatmod.build_brief(real_match, "_fixture", real_match["pov"])
        assert a == b, "the cached prefix drifts between turns - cache will miss"

    def test_the_brief_is_memoised_not_merely_deterministic(self, real_match):
        """
        Equality is not enough. build_facts() happens to be deterministic, so a
        brief rebuilt from scratch each turn still compares equal - and the
        assertion above passes while the memo is gone. This checks the memo
        itself: rebuilding ~4k tokens of analysis on every turn of every
        conversation is the cost the cache exists to avoid.
        """
        chatmod._brief_cache.clear()
        first = chatmod.build_brief(real_match, "_memotest", real_match["pov"])
        assert ("_memotest", real_match["pov"], None) in chatmod._brief_cache
        second = chatmod.build_brief(real_match, "_memotest", real_match["pov"])
        assert first is second, "the brief is being rebuilt rather than reused"
        chatmod._brief_cache.clear()

    def test_only_the_last_system_block_is_marked_cacheable(self, real_match):
        blocks, _ = chatmod.build_request(
            real_match, "_fixture", player=real_match["pov"], tick=None,
            question="hi")
        marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
        assert marked == [len(blocks) - 1], \
            "the breakpoint must sit on the last stable block"

    def test_the_volatile_half_is_in_messages_not_system(self, real_match):
        tick = real_match["frames"][5]["tick"]
        blocks, messages = chatmod.build_request(
            real_match, "_fixture", player=real_match["pov"], tick=tick,
            question="what now?")
        joined = " ".join(b["text"] for b in blocks)
        assert "THE MOMENT" not in joined, "per-tick text is in the cached prefix"
        assert "THE MOMENT" in messages[-1]["content"]

    def test_the_brief_is_big_enough_to_cache(self, brief):
        """Below ~512 tokens Anthropic silently declines to cache at all."""
        assert len(brief) > 2048, "brief too small for the cache minimum"


class TestHistory:
    def test_it_keeps_a_short_conversation_intact(self):
        h = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        assert chatmod.clamp_history(h) == h

    def test_it_drops_the_oldest_turns_first(self):
        h = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
             for i in range(100)]
        out = chatmod.clamp_history(h)
        assert len(out) <= chatmod.MAX_HISTORY_TURNS
        assert out[-1]["content"] == "m99", "the newest turn must survive"

    def test_it_never_starts_on_an_assistant_turn(self):
        """
        A reply with no question in front of it reads as the model talking to
        itself, and some providers reject it outright.

        The length matters: the slice has to actually LAND on an assistant turn
        for this to test anything. An even-length alternating history happens to
        slice cleanly and the check never fires - which is how an earlier
        version of this test passed while the guard was removed.
        """
        n = chatmod.MAX_HISTORY_TURNS + 1          # odd offset => slice starts mid-pair
        h = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
             for i in range(n)]
        raw = h[-chatmod.MAX_HISTORY_TURNS:]
        assert raw[0]["role"] == "assistant", "test setup no longer exercises the guard"
        assert chatmod.clamp_history(h)[0]["role"] == "user"

    def test_it_caps_total_size(self):
        h = [{"role": "user", "content": "x" * 5000},
             {"role": "assistant", "content": "y" * 5000}] * 10
        out = chatmod.clamp_history(h)
        assert sum(len(m["content"]) for m in out) <= chatmod.MAX_HISTORY_CHARS

    def test_it_drops_junk_entries(self):
        h = [{"role": "system", "content": "nope"},
             {"role": "user", "content": ""},
             {"role": "user", "content": "real"}]
        assert chatmod.clamp_history(h) == [{"role": "user", "content": "real"}]

    def test_none_is_not_an_error(self):
        assert chatmod.clamp_history(None) == []


class TestRoleSlot:
    """
    Roles are designed for, not built. These pin the seam so adding them later
    is a data change rather than a refactor.
    """

    def test_role_guidance_ships_empty(self):
        assert chatmod.ROLE_GUIDANCE == {}, \
            "role rules landed without the design pass they need"

    def test_an_unknown_role_changes_nothing(self, real_match):
        plain = chatmod.build_brief(real_match, "_fixture", real_match["pov"])
        with_role = chatmod.build_brief(real_match, "_fixture",
                                        real_match["pov"], role="awper")
        assert plain == with_role

    def test_a_known_role_is_threaded_into_the_brief(self, real_match, monkeypatch):
        monkeypatch.setitem(chatmod.ROLE_GUIDANCE, "awper",
                            "Judge them on opening picks held from range.")
        chatmod._brief_cache.clear()
        out = chatmod.build_brief(real_match, "_fixture", real_match["pov"],
                                  role="awper")
        assert "awper" in out.lower() and "opening picks" in out
        chatmod._brief_cache.clear()


class TestQuestionLimits:
    def test_a_long_question_is_clipped_not_dropped(self, real_match):
        _, messages = chatmod.build_request(
            real_match, "_fixture", player=real_match["pov"], tick=None,
            question="q" * (chatmod.MAX_QUESTION * 3))
        body = messages[-1]["content"]
        assert "truncated" in body
        assert len(body) < chatmod.MAX_QUESTION * 3

    def test_a_tick_outside_the_match_is_refused_not_guessed(self, real_match):
        """
        snapshot() answers with the NEAREST frame, which is right for a playhead
        between samples and wrong for a tick that isn't in the match. Before the
        bounds check, tick=1e9 produced "Round 5, 15624478s in, 0 HP" and the
        model would have reasoned about that as fact.
        """
        _, messages = chatmod.build_request(
            real_match, "_fixture", player=real_match["pov"], tick=10**9,
            question="hi")
        body = messages[-1]["content"]
        assert "outside the recorded part" in body
        assert "15624478" not in body, "a fabricated timestamp reached the prompt"

    def test_a_tick_inside_the_match_still_resolves(self, real_match):
        """The bounds check must not reject legitimate between-sample ticks."""
        mid = real_match["frames"][len(real_match["frames"]) // 2]["tick"] + 7
        _, messages = chatmod.build_request(
            real_match, "_fixture", player=real_match["pov"], tick=mid,
            question="hi")
        assert "THE MOMENT" in messages[-1]["content"]
