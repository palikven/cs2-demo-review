"""
Tests for the three signals that are easiest to state wrongly.

Each of these produces a number that reads as a verdict when it isn't:

  kit    "you died holding utility 38% of the time" sounds like a habit. On the
         bundled match it is SECOND BEST of ten, so reported alone it coaches a
         strength as a weakness. The table is not decoration.

  aim    pitch measures the map as much as the player - looking down is correct
         on a ramp. The both-sides gate is the only thing separating "you stare
         at the floor" from "you played the position with the ramp in it".

  comms  the demo proves the mic was open. It does not prove anything useful was
         said, and the difference is the whole claim.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "parser"))

import aim  # noqa: E402
import comms  # noqa: E402
import kit  # noqa: E402
from conftest import fplayer, frame, kill, make_match  # noqa: E402


# --------------------------------------------------------------------------
# kit
# --------------------------------------------------------------------------
class TestKit:
    def test_only_real_utility_counts(self):
        k = dict(kill(100, 1, "a", "b"),
                 victim_inv=["AK-47", "Flashbang", "Decoy Grenade", "knife"])
        assert kit.held(k) == ["Flashbang"], "a decoy is not a missed grenade"

    def test_a_repeek_gave_them_a_turn(self):
        assert kit.had_a_turn({"repeek": True}) == "re-peek"

    def test_a_sightline_neither_crossed_gave_them_a_turn(self):
        assert kit.had_a_turn(
            {"victim_closed": 10, "attacker_closed": -5}) == "static sightline"

    def test_being_run_into_did_not(self):
        """
        Someone sprinting round a corner onto you is not a duel you could have
        prepped. Counting it turns 'you had a flash' into noise on every death.
        """
        assert kit.had_a_turn(
            {"victim_closed": 5, "attacker_closed": 600}) is None

    def test_closure_is_measured_by_magnitude_not_sign(self):
        """
        Backing away 600 units is not standing in a sightline. A signed
        comparison passes it as static because -600 < 50.
        """
        assert kit.had_a_turn(
            {"victim_closed": -600, "attacker_closed": 0}) is None

    def test_the_table_covers_everyone_who_died(self, real_match):
        t = kit.match_table(real_match)
        assert len(t) > 1, "one row is not a benchmark"
        assert all(0 <= r["share"] <= 1 for r in t.values())

    def test_the_profile_reports_rank_not_just_share(self, real_match):
        """
        The share alone flips the verdict on this match. Rank is what stops it.
        """
        p = kit.profile(real_match, real_match["pov"], [])
        assert p["rank"] is not None and p["of"] > 1
        assert "table" in p and len(p["table"]) > 1

    def test_only_deaths_with_a_turn_are_coachable(self, analyze_mod, real_match):
        a = analyze_mod.analyze(real_match)
        p = a.get("kit") or {}
        for h in p.get("with_a_turn") or []:
            assert h["why"] in ("re-peek", "static sightline")
            assert h["held"], "listed a death with no utility held"

    def test_the_fact_sheet_states_the_rank_beside_the_share(
            self, analyze_mod, real_match):
        import coach
        a = analyze_mod.analyze(real_match)
        if not a.get("kit"):
            pytest.skip("fixture has no priced deaths")
        sheet = coach.build_facts(a, real_match)
        line = next(l for l in sheet.splitlines() if "died holding utility" in l)
        assert "rank" in line and "LOWER is better" in line


# --------------------------------------------------------------------------
# aim
# --------------------------------------------------------------------------
def pitch_match(rows):
    """rows: [(tick, name, side, x, y, pitch), ...]"""
    frames = {}
    for tick, name, side, x, y, pi in rows:
        p = fplayer(name, x=x, y=y, side=side)
        p["pi"] = pi
        frames.setdefault(tick, []).append(p)
    fr = [frame(t, *ps, rnd=1) for t, ps in sorted(frames.items())]
    m = make_match([], fr, pov=rows[0][1])
    m["tickrate"] = 64
    m["damages"] = []
    return m


class TestAim:
    def test_a_standing_player_is_not_sampled(self):
        """
        Holding an angle aimed at a doorway below you is good play, and idling
        in spawn is nothing at all. Both look like 'staring at the floor'.
        """
        m = pitch_match([(0, "a", "t", 0, 0, 30.0), (32, "a", "t", 0, 0, 30.0)])
        assert not aim.samples(m)["a"]["t"]

    def test_a_moving_player_is_sampled(self):
        m = pitch_match([(0, "a", "t", 0, 0, 5.0), (32, "a", "t", 300, 0, 5.0)])
        assert aim.samples(m)["a"]["t"] == [5.0]

    def test_a_dead_player_is_not_sampled(self):
        rows = [(0, "a", "t", 0, 0, 5.0), (32, "a", "t", 300, 0, 40.0)]
        m = pitch_match(rows)
        m["frames"][1]["players"][0]["hp"] = 0
        assert aim.samples(m)["a"]["t"] == []

    def test_frames_just_after_being_shot_are_dropped(self):
        """A hit swings the view; those degrees are recoil, not aim."""
        m = pitch_match([(0, "a", "t", 0, 0, 5.0), (32, "a", "t", 300, 0, 40.0)])
        m["damages"] = [{"t": 20, "v": "a", "a": "b", "d": 30}]
        assert aim.samples(m)["a"]["t"] == []

    def test_the_gate_needs_both_sides(self, real_match):
        """
        A player high on one side only is describing a position. On the bundled
        match the POV is exactly that - above the median as T, below as CT.
        """
        table = aim.match_table(real_match)
        if len(table) < 3:
            pytest.skip("fixture has too few players for a ranking")
        for name in table:
            p = aim.profile(real_match, name)
            if p.get("claimable"):
                assert p["above_on_t"] and p["above_on_ct"]

    def test_the_gate_needs_a_margin_and_needs_both_sides(self):
        """
        Both halves of the gate, on one synthetic match built so each player
        lands on exactly one interesting case.

            T medians:  0, 0, 0, 0.8, 8, 8   -> match median 0.4
            CT medians: 0, 0, 0, 0.8, 0,  8  -> match median 0.0

          marginal  T +0.4 over the median. Inside the margin, so NOT a habit -
                    but a build with no margin calls it one.
          oneside   T +7.6 over, CT +0.0 over. Clearly high on one side and not
                    the other, which is what "you played the ramp position"
                    looks like - a gate joined with `or` coaches it.
          habit     high on both. If this is not claimable the gate never fires
                    at all, which is indistinguishable from broken.

        Synthetic rather than from the fixture on purpose: whether a real match
        happens to contain a marginal player is luck, and a test that depends on
        luck passes against a build with the margin deleted. It did - this test
        replaces one that asserted a player sitting exactly ON the median was
        rejected, which is true with or without the margin.
        """
        pitches = {
            "low1": (0.0, 0.0), "low2": (0.0, 0.0), "low3": (0.0, 0.0),
            "marginal": (0.8, 0.8), "oneside": (8.0, 0.0), "habit": (8.0, 8.0),
        }
        rows, tick = [], 0
        for name, (t_pitch, ct_pitch) in pitches.items():
            for side, pitch in (("t", t_pitch), ("ct", ct_pitch)):
                for i in range(aim.MIN_SAMPLES_PER_SIDE + 5):
                    rows.append((tick, name, side, i * 300.0, 0.0, pitch))
                    tick += 32
        m = pitch_match(rows)

        assert aim.profile(m, "marginal")["above_on_t"] is False, \
            "a player inside the margin is being called a habit"
        assert aim.profile(m, "marginal")["claimable"] is False

        one = aim.profile(m, "oneside")
        assert one["above_on_t"] is True and one["above_on_ct"] is False
        assert one["claimable"] is False, \
            "high on one side only - that describes a position, not a player"

        assert aim.profile(m, "habit")["claimable"] is True, \
            "the gate never fires - indistinguishable from broken"

    def test_the_gate_does_not_pass_most_of_the_match(self, real_match):
        table = aim.match_table(real_match)
        if len(table) < 3:
            pytest.skip("fixture has too few players")
        claimable = [n for n in table if aim.profile(real_match, n).get("claimable")]
        assert len(claimable) < len(table) / 2, \
            "the gate passes half the match - it is not discriminating"

    def test_an_unclaimable_reading_still_reports_the_number(self, real_match):
        """Refusing to coach it is not the same as hiding it."""
        p = aim.profile(real_match, real_match["pov"])
        if not p:
            pytest.skip("no pitch data in the fixture")
        assert p["median"] is not None
        if not p["claimable"]:
            assert "do not coach it" in p["note"].lower()

    def test_it_never_calls_itself_crosshair_placement(self, real_match):
        p = aim.profile(real_match, real_match["pov"])
        if not p:
            pytest.skip("no pitch data in the fixture")
        assert "not crosshair placement in full" in p["note"].lower()

    def test_a_match_with_no_pitch_reports_nothing(self, real_match):
        stripped = dict(real_match)
        stripped["frames"] = [
            {**f, "players": [{k: v for k, v in p.items() if k != "pi"}
                              for p in f["players"]]}
            for f in real_match["frames"]]
        assert aim.profile(stripped, real_match["pov"]) == {}


# --------------------------------------------------------------------------
# comms
# --------------------------------------------------------------------------
def voice_index(**utterances):
    return {"tickrate": 64,
            "players": [{"name": n, "utterances": u}
                        for n, u in utterances.items()]}


class TestComms:
    def test_speaking_after_a_death_counts(self):
        m = make_match([kill(1000, 1, "b", "a")],
                       [frame(1000, fplayer("a"), fplayer("b", side="ct"))])
        m["rounds"] = [{"round_num": 1, "start": 0, "freeze_end": 100,
                        "end": 5000, "official_end": 5000}]
        v = voice_index(a=[{"s": 1050, "e": 1200, "a": 0}])
        assert comms.after_death_table(m, v)["a"] == {
            "deaths": 1, "spoke": 1, "share": 1.0}

    def test_a_death_at_the_end_of_the_round_is_excluded(self):
        """
        The round is over; there is nothing left to call. Counting it marks a
        player down for the rounds where silence was right.
        """
        m = make_match([kill(4900, 1, "b", "a")],
                       [frame(4900, fplayer("a"), fplayer("b", side="ct"))])
        m["rounds"] = [{"round_num": 1, "start": 0, "freeze_end": 100,
                        "end": 5000, "official_end": 5000}]
        assert comms.after_death_table(m, voice_index(a=[])) == {}

    def test_speech_after_the_window_does_not_count(self):
        m = make_match([kill(1000, 1, "b", "a")],
                       [frame(1000, fplayer("a"), fplayer("b", side="ct"))])
        m["rounds"] = [{"round_num": 1, "start": 0, "freeze_end": 100,
                        "end": 9000, "official_end": 9000}]
        v = voice_index(a=[{"s": 1000 + 64 * 20, "e": 1000 + 64 * 21, "a": 0}])
        assert comms.after_death_table(m, v)["a"]["spoke"] == 0

    def test_before_a_death_only_the_players_own_side_counts(self):
        """Hearing the enemy talk is not your team communicating."""
        m = make_match([kill(1000, 1, "e", "a")],
                       [frame(1000, fplayer("a", side="t"),
                              fplayer("mate", side="t"),
                              fplayer("e", side="ct"))])
        m["rounds"] = [{"round_num": 1, "start": 0, "freeze_end": 100,
                        "end": 9000, "official_end": 9000}]
        v = voice_index(mate=[{"s": 950, "e": 999, "a": 0}],
                        e=[{"s": 950, "e": 999, "a": 0}])
        got = comms.before_deaths(m, v, "a")[0]
        assert got["teammates"] == ["mate"]

    def test_it_says_the_mic_was_open_not_that_anything_was_said(
            self, real_match):
        """
        The wording is the test. Someone swearing at their monitor scores the
        same as someone calling 'two A, one low'.
        """
        v = real_match.get("_voice")
        if not v:
            pytest.skip("fixture has no voice index")
        p = comms.profile(real_match, v, real_match["pov"])
        assert "mic was open" in p["note"].lower()
        assert "not what was said" in p["note"].lower()

    def test_no_voice_index_means_no_profile_rather_than_zero(self, real_match):
        """A match with no comms extracted has not got a 0% call rate."""
        assert comms.profile(real_match, None, real_match["pov"]) == {}
        assert comms.profile(real_match, {"players": []},
                             real_match["pov"]) == {}


class TestSharedLoader:
    def test_every_entry_point_attaches_the_voice_index(self):
        """
        analyze.py's CLI, coach.py, narrate.py and the server all need this
        identically. When it lived in one of them the others produced a review
        with the comms section silently missing - the only symptom being a
        shorter sheet.
        """
        import analyze
        assert hasattr(analyze, "load_match")
        for mod in ("coach.py", "narrate.py"):
            src = (ROOT / "parser" / mod).read_text(encoding="utf-8")
            assert "load_match" in src, f"{mod} loads the match its own way"
        server = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        assert "load_match" in server, "the server loads the match its own way"


class TestCommandLines:
    """
    The entry points actually run.

    Every module here is imported and unit-tested, and none of that touched
    main(). A refactor that shared the match loader between the CLIs deleted a
    local the --emit path used two hundred lines further down; the whole suite
    passed and `python parser/analyze.py --emit` raised NameError on the first
    real invocation.

    These are smoke tests, not behaviour tests - they assert the process exits
    zero and wrote something. That is exactly the class of bug they exist for.
    """

    @pytest.mark.parametrize("script,args", [
        ("analyze.py", []),
        ("economy.py", []),
        ("flashes.py", []),
        ("kit.py", []),
        ("aim.py", []),
    ])
    def test_it_runs_against_the_bundled_match(self, script, args, tmp_path):
        import subprocess
        match = ROOT / "data" / "2v5.json"
        if not match.exists():
            pytest.skip("no parsed match on this machine")
        r = subprocess.run([sys.executable, str(ROOT / "parser" / script),
                            str(match), *args],
                           capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, f"{script} exited {r.returncode}:\n{r.stderr[-2000:]}"
        assert r.stdout.strip(), f"{script} printed nothing"

    def test_analyze_emit_writes_the_file(self, tmp_path):
        """The path that broke: --emit uses a local the refactor removed."""
        import json
        import shutil
        import subprocess
        src = ROOT / "data" / "2v5.json"
        if not src.exists():
            pytest.skip("no parsed match on this machine")
        work = tmp_path / "m.json"
        shutil.copy(src, work)
        r = subprocess.run([sys.executable, str(ROOT / "parser" / "analyze.py"),
                            str(work), "--emit"],
                           capture_output=True, text=True, timeout=180)
        assert r.returncode == 0, r.stderr[-2000:]
        out = work.with_name("m_analysis.json")
        assert out.exists(), "--emit exited cleanly without writing anything"
        assert json.loads(out.read_text(encoding="utf-8"))["deaths"]
