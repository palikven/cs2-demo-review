"""
The radar extractor, and the calibration file it writes.

Three things can go quietly wrong here and draw players in the wrong place:
the texture decoder (a wrong channel order looks plausible), the overview
parser (a mis-nested block loses a level), and the altitude ranges (a gap or an
overlap between two levels leaves a player on no panel, or on two).
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
try:
    import extract_radars as radars  # noqa: E402
except SystemExit:                    # the tool exits when vpk or pillow is not installed
    pytest.skip("pip install vpk pillow to run the radar tests", allow_module_level=True)
from PIL import Image  # noqa: E402  - after the guard, so a missing pillow skips rather than errors

CAL = json.loads((ROOT / "viewer" / "map_calibration.json").read_text(encoding="utf-8"))
TWO_LEVEL = ("de_nuke", "de_train", "de_vertigo")


class TestLz4:
    def test_an_overlapping_match_repeats_its_window(self):
        # 'abc' as literals, then a 6-byte match 3 bytes back
        assert radars.lz4_block(bytes([0x32]) + b"abc" + bytes([3, 0])) == b"abcabcabc"

    def test_a_zero_offset_is_refused_not_decoded(self):
        with pytest.raises(ValueError):
            radars.lz4_block(bytes([0x12]) + b"a" + bytes([0, 0]))


class TestOverviewParsing:
    TEXT = '''// HLTV overview
"de_x"
{
	"pos_x"		"-3453"	// upper left
	"verticalsections"
	{
		"default" // primary
		{
			"AltitudeMax" "10000"
			"AltitudeMin" "-495"
		}
		"lower"
		{
			"AltitudeMax" "-495"
			"AltitudeMin" "-10000"
		}
	}
}
'''

    def test_levels_come_out_in_file_order_with_their_ranges(self):
        ov = radars.parse_kv(self.TEXT)["de_x"]
        assert ov["pos_x"] == "-3453"
        assert list(ov["verticalsections"]) == ["default", "lower"]
        assert ov["verticalsections"]["lower"]["AltitudeMax"] == "-495"


class TestCalibration:
    @pytest.mark.parametrize("name", radars.POOL)
    def test_every_pool_map_has_a_calibration_and_its_radar_files(self, name):
        entry = CAL[name]
        assert {"pos_x", "pos_y", "scale"} <= set(entry)
        for level in entry.get("floors", [{"img": f"{name}.png"}]):
            path = ROOT / "radars" / level["img"]
            assert path.exists(), path
            assert Image.open(path).size == (1024, 1024)

    @pytest.mark.parametrize("name", TWO_LEVEL)
    def test_the_two_levels_tile_the_altitude_range_exactly(self, name):
        upper, lower = CAL[name]["floors"]
        assert (upper["name"], lower["name"]) == ("upper", "lower")
        assert upper["min"] == lower["max"], "a gap or an overlap between levels"
        assert lower["min"] < lower["max"] and upper["min"] < upper["max"]

    def test_single_level_maps_have_nothing_to_split(self):
        for name in radars.POOL:
            if name not in TWO_LEVEL:
                assert "floors" not in CAL[name], name


class TestDecoderAgainstTheCommittedRadar:
    def test_the_nuke_radar_decodes_to_the_committed_picture(self):
        """Needs CS2 installed. The committed PNG was checked pixel for pixel against this."""
        vpk = pytest.importorskip("vpk")
        try:
            pak_path = radars.find_pak()
        except SystemExit:
            pytest.skip("CS2 is not installed here")
        pak = vpk.open(str(pak_path))
        got = radars.read_vtex(
            pak.get_file("panorama/images/overheadmaps/de_nuke_radar_psd.vtex_c").read())
        want = Image.open(ROOT / "radars" / "de_nuke.png").convert("RGBA")
        assert got.size == want.size
        assert radars.picture(got) == radars.picture(want)
