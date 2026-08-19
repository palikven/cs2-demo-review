"""
Tests for the utility export (smokes, molotovs, flashes, HEs, throws, bomb).

Most of parse_utility() is a passthrough of what awpy already computed, so
these focus on the three places where we do real work and could quietly get it
wrong:

  * round_of()      - flash and HE detonations come from raw event tables that
                      carry no round number, so we derive one from tick windows
  * the end_tick fallback - a smoke still burning when the round ends never
                      gets an "expired" event
  * the invariants that catch a bad join, checked against real data

The join itself deserves a note. dem.smokes carries BOTH thrower_X/Y and X/Y;
the thrower columns are where the player stood, a median of ~800 units from
where the grenade landed. Drawing those would put every smoke in the wrong
place, and nothing except a human looking at the map would notice - hence
test_smoke_is_not_at_the_throwers_feet.
"""

import pytest


@pytest.fixture(scope="session")
def util(real_match):
    return real_match["utility"]


class TestRoundBackfill:
    """round_of() - flashes and HEs have no round_num of their own."""

    def _rounds(self):
        return [{"round_num": 1, "start": 100, "official_end": 200},
                {"round_num": 2, "start": 300, "end": 400}]

    def test_tick_inside_a_round(self, stats_mod):
        assert stats_mod.round_of(150, self._rounds()) == 1

    def test_boundaries_are_inclusive(self, stats_mod):
        rs = self._rounds()
        assert stats_mod.round_of(100, rs) == 1
        assert stats_mod.round_of(200, rs) == 1

    def test_falls_back_to_end_when_official_end_is_absent(self, stats_mod):
        assert stats_mod.round_of(350, self._rounds()) == 2

    def test_tick_between_rounds_belongs_to_neither(self, stats_mod):
        """Warmup and freeze time are not rounds; guessing would be worse."""
        assert stats_mod.round_of(250, self._rounds()) is None

    def test_none_tick_is_none(self, stats_mod):
        assert stats_mod.round_of(None, self._rounds()) is None

    def test_no_round_table_does_not_crash(self, stats_mod):
        assert stats_mod.round_of(150, []) is None


class TestTimedUtility:
    """
    Direct unit tests for timed_utility().

    These exist because the fixture-based tests below read PRE-PARSED data:
    breaking parse_demo.py cannot make them fail, since the fixture would have
    to be regenerated (a four-minute demo parse) to notice. They guard the data
    is correct; these guard the code that produces it.
    """

    def _row(self, **kw):
        row = {"start_tick": 1000, "end_tick": 2000, "X": 10.0, "Y": 20.0,
               "round_num": 1, "thrower_name": "me", "thrower_side": "t"}
        row.update(kw)
        return row

    def _rounds(self):
        return [{"round_num": 1, "start": 900, "official_end": 1500}]

    def test_uses_the_landing_position_not_the_throwers(self, stats_mod):
        """The one that catches reading thrower_X/Y by mistake."""
        row = self._row(thrower_X=999.0, thrower_Y=888.0)
        out = stats_mod.timed_utility([row], 18, self._rounds(), 64)
        assert (out[0]["x"], out[0]["y"]) == (10.0, 20.0)

    def test_carries_thrower_identity_without_their_position(self, stats_mod):
        out = stats_mod.timed_utility([self._row()], 18, self._rounds(), 64)
        assert out[0]["by"] == "me" and out[0]["side"] == "t"
        assert "thrower_X" not in out[0] and "thrower_Y" not in out[0]

    def test_missing_end_tick_falls_back_to_nominal_duration(self, stats_mod):
        """A smoke still up at round end never gets an 'expired' event."""
        out = stats_mod.timed_utility([self._row(end_tick=None)], 5, [], 64)
        assert out[0]["e"] == 1000 + 5 * 64

    def test_fallback_is_clamped_to_the_end_of_the_round(self, stats_mod):
        """18s past a round that ended in 8s would burn into the next round."""
        out = stats_mod.timed_utility([self._row(end_tick=None)], 18,
                                      self._rounds(), 64)
        assert out[0]["e"] == 1500

    def test_a_recorded_end_tick_is_never_overridden(self, stats_mod):
        out = stats_mod.timed_utility([self._row()], 18, self._rounds(), 64)
        assert out[0]["e"] == 2000

    def test_entries_without_a_position_are_dropped(self, stats_mod):
        assert stats_mod.timed_utility([self._row(X=None)], 18, [], 64) == []

    def test_empty_input_is_not_an_error(self, stats_mod):
        assert stats_mod.timed_utility([], 18, [], 64) == []


class TestExportedShape:
    def test_every_kind_is_present(self, util):
        assert set(util) == {"smokes", "fires", "flashes", "hes", "throws",
                             "blinds", "shots"}

    def test_timed_utility_has_a_valid_window(self, util):
        for key in ("smokes", "fires"):
            for e in util[key]:
                assert e["s"] < e["e"], f"{key} {e} has a non-positive lifetime"

    def test_smoke_lifetimes_are_physically_plausible(self, util, real_match):
        """A smoke that lasts 90s means the end_tick fallback went wrong."""
        rate = real_match["tickrate"]
        for e in util["smokes"]:
            assert 0 < (e["e"] - e["s"]) / rate <= 25

    def test_fire_lifetimes_are_physically_plausible(self, util, real_match):
        rate = real_match["tickrate"]
        for e in util["fires"]:
            assert 0 < (e["e"] - e["s"]) / rate <= 10

    def test_detonations_carry_a_round(self, util):
        """These come from event tables with no round_num - round_of fills it."""
        for key in ("flashes", "hes"):
            assert all(e.get("r") is not None for e in util[key]), key

    def test_every_event_has_a_position(self, util):
        for key, items in util.items():
            # blinds are the one kind with no position, and correctly so:
            # player_blind records who was caught and for how long, not where
            # anybody stood. Nothing draws them, so there is nothing to place.
            # blinds record who was caught, not where; shots carry an origin
            # but are a ray rather than a placed area, and nothing treats them
            # as one.
            if key in ("blinds", "shots"):
                continue
            for e in items:
                if key == "throws":
                    assert len(e["p"]) >= 2
                else:
                    assert e["x"] is not None and e["y"] is not None

    def test_shots_carry_an_origin_an_angle_and_a_spray_index(self, util):
        """
        The four fields a tracer and a spray curve need. A shot with no angle
        is undrawable; one with no recoil_index is just a shot count, which
        weapon_fire already gave us for free.
        """
        for e in util["shots"]:
            assert e["x"] is not None and e["y"] is not None
            # Source does not normalise yaw, so it drifts slightly outside
            # [-180, 180] - the bundled match has -180.3. cos/sin are periodic
            # so that draws correctly; test_spray.py proves the angles actually
            # point at what was shot. This only checks it is a real number.
            assert -360 <= e["a"] <= 360
            assert e["i"] >= 0
            assert e["by"]

    def test_blinds_carry_who_caught_them_and_for_how_long(self, util):
        """
        The three fields the coaching depends on. A blind with no victim cannot
        be told apart from a team-flash, which is the whole point of the export.
        """
        for e in util["blinds"]:
            assert e["by"] and e["who"]
            assert e["d"] > 0 and e["r"] is not None


class TestJoinSanity:
    """The checks that would have caught the two traps in this data."""

    def test_smoke_is_not_at_the_throwers_feet(self, util, real_match):
        """
        dem.smokes has thrower_X/Y AND X/Y. Using the wrong pair puts every
        smoke on the player instead of where it landed, which still *renders*
        and so passes every other test here.

        A thrown smoke travels; if the exported position sat on top of the
        thrower for most smokes we'd have grabbed the wrong column.
        """
        # the thrower's position is recoverable from the frames at that tick
        frames = {f["tick"]: f for f in real_match["frames"]}
        checked = far = 0
        for s in util["smokes"]:
            fr = min(frames.values(), key=lambda f: abs(f["tick"] - s["s"]))
            p = next((p for p in fr["players"] if p["n"] == s.get("by")), None)
            if not p:
                continue
            checked += 1
            if ((p["x"] - s["x"]) ** 2 + (p["y"] - s["y"]) ** 2) ** 0.5 > 150:
                far += 1
        assert checked, "no smokes could be matched to a thrower"
        assert far / checked > 0.5, "smokes look pinned to the thrower's feet"

    def test_throw_counts_match_detonation_counts(self, util):
        """
        A throw arc and its detonation come from different tables, so if the
        arcs were mis-grouped (entity ids ARE recycled across rounds) the
        totals would drift apart.
        """
        by_kind = {}
        for t in util["throws"]:
            by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
        assert by_kind.get("smoke") == len(util["smokes"])
        assert by_kind.get("flash") == len(util["flashes"])
        assert by_kind.get("he") == len(util["hes"])

    def test_throw_arcs_actually_travel(self, util):
        """A trimmed-to-nothing arc would be a start point repeated."""
        for t in util["throws"]:
            a, b = t["p"][0], t["p"][-1]
            assert (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 > 1

    def test_throw_windows_are_ordered(self, util):
        assert all(t["s"] <= t["e"] for t in util["throws"])

    def test_kinds_are_the_ones_the_viewer_draws(self, util, stats_mod):
        known = set(stats_mod.GRENADE_KINDS.values())
        assert {t["kind"] for t in util["throws"]} <= known


class TestBomb:
    def test_only_meaningful_events_are_kept(self, real_match):
        """pickup/drop fire constantly and tell a reviewer nothing."""
        assert {b["event"] for b in real_match["bomb"]} <= {"plant", "defuse", "detonate"}

    def test_plants_have_a_site(self, real_match):
        for b in real_match["bomb"]:
            if b["event"] == "plant":
                assert b.get("site")

    def test_a_plant_often_has_no_defuse_or_detonate(self, real_match):
        """
        Pins the data fact behind a real rendering bug.

        If every CT dies after the plant the round is already decided, and the
        demo never records the bomb going off - so the plant has no resolution
        event at all. In the full match only 4 of 13 plants have one.

        Anything deciding how long to show a planted bomb must therefore fall
        back to the round's end rather than keying on defuse/detonate. Doing
        the latter left every unresolved plant on the map for the rest of the
        match: by the last round the viewer was drawing nine bombs at once.
        """
        plants = [b for b in real_match["bomb"] if b["event"] == "plant"]
        resolved_rounds = {b["r"] for b in real_match["bomb"]
                           if b["event"] in ("defuse", "detonate")}
        unresolved = [p for p in plants if p["r"] not in resolved_rounds]
        assert plants, "no plants in this slice"
        assert unresolved, ("every plant here resolves, so this fixture can no "
                            "longer guard the round-end fallback - re-cut it "
                            "from a match that has an unresolved plant")

    def test_every_plant_belongs_to_a_known_round(self, real_match):
        """The round-end fallback needs a round to look up."""
        rounds = {r["round_num"] for r in real_match["rounds"]}
        for b in real_match["bomb"]:
            assert b.get("r") in rounds, f"{b['event']} at {b['t']} has round {b.get('r')}"

    def test_plants_land_inside_their_round(self, real_match):
        by_round = {r["round_num"]: r for r in real_match["rounds"]}
        for b in real_match["bomb"]:
            rr = by_round[b["r"]]
            end = rr.get("official_end") or rr.get("end")
            assert rr["start"] <= b["t"] <= end, \
                f"{b['event']} at {b['t']} is outside round {b['r']}"
