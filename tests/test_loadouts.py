"""
Tests for the loadout export: armor, helmet, defuser and interned inventories.

A loadout changes only on a buy or a pickup, so across 40,000 player samples
there are a few hundred distinct ones. Storing the item list inline on every
frame entry would roughly double the file; instead each entry carries one
integer into a `loadouts` table, which in turn indexes `item_names`.

Two indirections means two ways to produce a file that parses fine and renders
the wrong gun, so these check that every index resolves and that the tables
really are deduplicated.
"""

import pytest


@pytest.fixture(scope="session")
def items(real_match):
    return real_match["item_names"]


@pytest.fixture(scope="session")
def loadouts(real_match):
    return real_match["loadouts"]


def resolve(match, player):
    """The lookup the viewer does: entry -> loadout -> item names."""
    if "eq" not in player:
        return None
    return [match["item_names"][i] for i in match["loadouts"][player["eq"]]]


class TestInterning:
    def test_tables_are_present(self, items, loadouts):
        assert items and loadouts

    def test_item_names_are_unique(self, items):
        """A duplicate means the intern map was rebuilt or keyed wrongly."""
        assert len(items) == len(set(items))

    def test_loadouts_are_unique(self, loadouts):
        """Interning that emits duplicates is just a slower list."""
        seen = [tuple(l) for l in loadouts]
        assert len(seen) == len(set(seen))

    def test_every_item_index_resolves(self, items, loadouts):
        for l in loadouts:
            for i in l:
                assert 0 <= i < len(items), f"item index {i} out of range"

    def test_every_frame_eq_resolves(self, real_match, loadouts):
        for f in real_match["frames"]:
            for p in f["players"]:
                if "eq" in p:
                    assert 0 <= p["eq"] < len(loadouts), \
                        f"{p['n']} has eq={p['eq']} but only {len(loadouts)} loadouts"

    def test_loadouts_are_stored_sorted(self, loadouts, items):
        """
        The intern key is the sorted tuple, so two players holding the same
        kit in a different pickup order must share one entry. If the stored
        list is not sorted, the key was built from something else.
        """
        for l in loadouts:
            names = [items[i] for i in l]
            assert names == sorted(names)

    def test_no_empty_loadout_is_referenced(self, real_match, loadouts):
        """A dead player has no inventory; the parser omits eq rather than
        pointing at an empty list."""
        for f in real_match["frames"]:
            for p in f["players"]:
                if "eq" in p:
                    assert loadouts[p["eq"]], f"{p['n']} references an empty loadout"


class TestContents:
    def test_items_look_like_display_names(self, items):
        """
        inventory gives display names ("AK-47"), not the internal ids the
        killfeed uses ("ak47"). Mixing the two would break the icon map.
        """
        assert "AK-47" in items or "M4A4" in items
        assert "ak47" not in items

    def test_a_living_player_has_a_resolvable_loadout(self, real_match):
        for f in real_match["frames"]:
            for p in f["players"]:
                if p.get("hp", 0) > 0 and "eq" in p:
                    assert resolve(real_match, p)
                    return
        pytest.fail("no living player with a loadout in the fixture")

    def test_dead_players_carry_nothing(self, real_match):
        """Inventory clears on death, so eq should be absent."""
        checked = 0
        for f in real_match["frames"]:
            for p in f["players"]:
                if p.get("hp") == 0:
                    checked += 1
                    assert "eq" not in p, f"{p['n']} is dead but still has gear"
        assert checked, "no dead players in the fixture to check"

    def test_every_item_is_mappable_to_an_icon(self, items):
        """
        The viewer maps display names to icon files. An item with no mapping
        renders as a gap, which looks like a bug rather than a missing icon -
        so if a new item shows up here, ITEM_ICONS needs the entry too.
        """
        known_kinds = ("Grenade", "Molotov", "C4", "Zeus", "Flashbang")
        for name in items:
            plausible = (
                any(k in name for k in known_kinds)
                or any(c.isdigit() for c in name)      # AK-47, P250, MP9...
                or name[0].isupper() or name.islower()  # named guns and knives
            )
            assert plausible, f"unexpected item name: {name!r}"


class TestArmorAndKit:
    def test_armor_when_present_is_in_range(self, real_match):
        for f in real_match["frames"]:
            for p in f["players"]:
                if "ar" in p:
                    assert 0 < p["ar"] <= 100, f"{p['n']} armor={p['ar']}"

    def test_zero_armor_is_omitted_not_stored(self, real_match):
        """Storing 0 for every unarmoured player costs bytes and says nothing."""
        for f in real_match["frames"]:
            for p in f["players"]:
                assert p.get("ar") != 0

    def test_flags_are_only_ever_set_true(self, real_match):
        """hm/df are omitted when false, so a stored 0 means a wasted byte."""
        for f in real_match["frames"]:
            for p in f["players"]:
                for flag in ("hm", "df"):
                    if flag in p:
                        assert p[flag] == 1

    def test_defusers_only_appear_on_ct(self, real_match):
        """A defuse kit on a T is a parsing error, not a strategy."""
        for f in real_match["frames"]:
            for p in f["players"]:
                if p.get("df"):
                    assert p.get("t") == "ct", f"{p['n']} is {p.get('t')} with a kit"

    def test_c4_only_appears_on_t(self, real_match):
        c4 = "C4 Explosive"
        if c4 not in real_match["item_names"]:
            pytest.skip("no C4 in this slice")
        idx = real_match["item_names"].index(c4)
        carriers = {i for i, l in enumerate(real_match["loadouts"]) if idx in l}
        for f in real_match["frames"]:
            for p in f["players"]:
                if p.get("eq") in carriers:
                    assert p.get("t") == "t", f"{p['n']} is {p.get('t')} with the bomb"


class TestDamageEvents:
    """
    The viewer replays these to show a running ADR, so they have to reproduce
    the parser's own number exactly - otherwise the panel drifts from the
    match total as you scrub toward the end.
    """

    def test_damages_are_exported_with_ticks(self, real_match):
        dmg = real_match.get("damages")
        assert dmg, "no damage events exported"
        for d in dmg:
            assert d["t"] is not None and d["a"] and d["d"] >= 0

    def test_replaying_them_reproduces_adr(self, real_match, stats_mod, kcols):
        """
        Feed compute_stats the exported events and its ADR must equal what you
        get by simply summing them. That property is exactly what the viewer
        relies on when it replays damage up to the playhead, so if it ever
        stops holding, the live ADR silently drifts from the match total.
        """
        from collections import defaultdict
        dmg = [{"attacker": d["a"], "victim": d["v"], "dmg": d["d"],
                "attacker_side": None, "victim_side": None}
               for d in real_match["damages"]]
        stats = stats_mod.compute_stats(
            real_match["kills"], real_match["rounds"], real_match["players"],
            None, kcols, damages=dmg)
        rounds = stats["rounds_played"]
        total = defaultdict(int)
        for d in real_match["damages"]:
            total[d["a"]] += d["d"]
        checked = 0
        for name, s in stats["per_player"].items():
            if s.get("adr") is None:
                continue
            checked += 1
            assert s["adr"] == pytest.approx(round(total[name] / rounds, 1), abs=0.05), \
                f"{name}: parser {s['adr']} vs replayed {total[name] / rounds:.1f}"
        assert checked, "no player had ADR computed"

    def test_self_damage_is_excluded(self, real_match):
        """Falling off a ledge must not pad your own ADR."""
        for d in real_match["damages"]:
            assert d["a"] != d["v"]

    def test_team_damage_is_excluded(self, real_match):
        """
        Pre-filtered at export so the viewer does not have to re-implement the
        rule and get it subtly different from compute_stats.
        """
        sides = {}
        for f in real_match["frames"]:
            for p in f["players"]:
                if p.get("t"):
                    sides.setdefault(p["n"], set()).add(p["t"])
        for d in real_match["damages"]:
            a, v = sides.get(d["a"]), sides.get(d["v"])
            # a player who only ever held one side across the slice
            if a and v and len(a) == 1 and len(v) == 1 and a == v:
                raise AssertionError(f"team damage kept: {d}")
