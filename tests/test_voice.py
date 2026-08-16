"""
Tests for the voice pipeline: the Ogg/Opus muxer and utterance grouping.

The muxer matters more than its size suggests. A container that is subtly
malformed does not raise anything - the browser simply refuses to decode it,
or decodes it and drifts out of sync partway through a 36 minute match. So
these walk the produced bytes the way a decoder would: re-deriving every page
CRC, checking granule positions advance by exactly one frame per packet, and
exercising the lacing edge cases that only appear on particular packet sizes.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "parser"))
import oggopus  # noqa: E402
import voice as voicemod  # noqa: E402


def pages(stream):
    """Walk an Ogg stream the way a decoder does, yielding parsed pages."""
    off, out = 0, []
    while off < len(stream):
        assert stream[off:off + 4] == b"OggS", f"lost sync at byte {off}"
        nseg = stream[off + 26]
        seg = stream[off + 27:off + 27 + nseg]
        body_len = sum(seg)
        length = 27 + nseg + body_len
        raw = stream[off:off + length]
        out.append({
            "header_type": raw[5],
            "granule": struct.unpack("<q", raw[6:14])[0],
            "serial": struct.unpack("<I", raw[14:18])[0],
            "seq": struct.unpack("<I", raw[18:22])[0],
            "crc": struct.unpack("<I", raw[22:26])[0],
            "segments": list(seg),
            "body": raw[27 + nseg:],
            "raw": raw,
        })
        off += length
    return out


def packet(n, fill=0x70):
    """A stand-in Opus packet; 0x70 is the TOC byte CS2 voice actually uses."""
    return bytes([fill]) + b"\xAA" * (n - 1)


class TestContainer:
    def test_starts_with_the_two_header_pages(self):
        ps = pages(oggopus.mux([packet(60)]))
        assert ps[0]["body"][:8] == b"OpusHead"
        assert ps[1]["body"][:8] == b"OpusTags"

    def test_opushead_describes_the_stream(self):
        head = pages(oggopus.mux([packet(60)]))[0]["body"]
        assert head[8] == 1                                    # version
        assert head[9] == 1                                    # mono
        assert struct.unpack("<H", head[10:12])[0] == oggopus.PRE_SKIP
        assert struct.unpack("<I", head[12:16])[0] == 48000
        assert head[18] == 0                                   # mapping family

    def test_bos_and_eos_flags(self):
        ps = pages(oggopus.mux([packet(60)] * 3))
        assert ps[0]["header_type"] & 0x02, "first page must be BOS"
        assert ps[-1]["header_type"] & 0x04, "last page must be EOS"
        assert not any(p["header_type"] & 0x02 for p in ps[1:])

    def test_page_sequence_is_contiguous(self):
        ps = pages(oggopus.mux([packet(60)] * 900))
        assert [p["seq"] for p in ps] == list(range(len(ps)))

    def test_serial_is_uniform_and_settable(self):
        ps = pages(oggopus.mux([packet(60)] * 5, serial=4242))
        assert {p["serial"] for p in ps} == {4242}

    def test_every_page_crc_verifies(self):
        """Ogg's CRC32 is not zlib's; getting it wrong is silent until playback."""
        for p in pages(oggopus.mux([packet(n) for n in range(40, 140)])):
            raw = bytearray(p["raw"])
            raw[22:26] = b"\x00\x00\x00\x00"
            assert oggopus._crc32(bytes(raw)) == p["crc"]

    def test_crc_is_not_the_zlib_one(self):
        import zlib
        data = b"OggS-sample-page-bytes"
        assert oggopus._crc32(data) != zlib.crc32(data)

    def test_crc_matches_an_independent_implementation(self):
        """
        test_every_page_crc_verifies re-derives the checksum with the same
        function, so it only proves self-consistency - a wrong lookup table
        would agree with itself and still produce files no player accepts.
        This checks the table-driven version against a naive bitwise one,
        which is the realistic failure mode.

        (End to end, the real proof is that a browser decodes the output:
        Chrome rejects pages whose CRC does not verify.)
        """
        def bitwise(data):
            crc = 0
            for byte in data:
                crc ^= byte << 24
                for _ in range(8):
                    crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF \
                        if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
            return crc

        for sample in (b"", b"a", b"123456789", b"OggS" + bytes(range(64)),
                       bytes(range(256)) * 3):
            assert oggopus._crc32(sample) == bitwise(sample), sample[:16]


class TestGranule:
    def test_headers_have_zero_granule(self):
        ps = pages(oggopus.mux([packet(60)] * 4))
        assert ps[0]["granule"] == 0 and ps[1]["granule"] == 0

    def test_final_granule_is_preskip_plus_all_frames(self):
        n = 250
        ps = pages(oggopus.mux([packet(60)] * n))
        assert ps[-1]["granule"] == oggopus.PRE_SKIP + n * 480

    def test_granule_never_goes_backwards(self):
        g = [p["granule"] for p in pages(oggopus.mux([packet(70)] * 800))][2:]
        assert all(b >= a for a, b in zip(g, g[1:]))

    def test_frame_size_is_honoured(self):
        """20ms frames would be 960 samples; drift here desyncs the whole match."""
        ps = pages(oggopus.mux([packet(60)] * 10, samples_per_packet=960))
        assert ps[-1]["granule"] == oggopus.PRE_SKIP + 10 * 960


class TestLacing:
    def test_small_packet_is_one_segment(self):
        ps = pages(oggopus.mux([packet(60)]))
        assert ps[2]["segments"] == [60]

    def test_packet_of_exactly_255_needs_a_trailing_zero(self):
        """
        Without the terminating 0 the decoder keeps reading into the next
        packet. Real CS2 voice packets are 38-136 bytes so this never fires in
        practice - which is exactly why it needs a test.
        """
        ps = pages(oggopus.mux([packet(255)]))
        assert ps[2]["segments"] == [255, 0]

    def test_packet_larger_than_255(self):
        ps = pages(oggopus.mux([packet(600)]))
        assert ps[2]["segments"] == [255, 255, 90]

    def test_segment_table_never_exceeds_the_page_limit(self):
        for p in pages(oggopus.mux([packet(60)] * 5000)):
            assert len(p["segments"]) <= 255

    def test_body_length_matches_the_segment_table(self):
        for p in pages(oggopus.mux([packet(n % 90 + 40) for n in range(1200)])):
            assert len(p["body"]) == sum(p["segments"])

    def test_all_packets_survive_the_round_trip(self):
        """Concatenated page bodies must equal the concatenated input."""
        pk = [packet(n % 90 + 40, fill=n % 251) for n in range(1500)]
        body = b"".join(p["body"] for p in pages(oggopus.mux(pk))[2:])
        assert body == b"".join(pk)

    def test_a_single_packet_still_gets_an_eos_page(self):
        ps = pages(oggopus.mux([packet(38)]))
        assert len(ps) == 3 and ps[-1]["header_type"] & 0x04

    def test_empty_input_produces_headers_only(self):
        ps = pages(oggopus.mux([]))
        assert len(ps) == 2


class TestUtteranceGrouping:
    RATE = 64

    def test_contiguous_speech_is_one_utterance(self):
        pk = [(t, b"x") for t in range(100, 160)]
        _, utt = voicemod.group_utterances(pk, self.RATE)
        assert len(utt) == 1
        assert utt[0]["s"] == 100 and utt[0]["e"] == 159 and utt[0]["a"] == 0.0

    def test_a_long_silence_splits(self):
        pk = [(t, b"x") for t in range(100, 120)] + \
             [(t, b"x") for t in range(500, 520)]
        _, utt = voicemod.group_utterances(pk, self.RATE)
        assert len(utt) == 2
        assert (utt[0]["s"], utt[1]["s"]) == (100, 500)

    def test_a_short_gap_does_not_split(self):
        """Half a second is the threshold; a breath mid-sentence is shorter."""
        gap = int(0.3 * self.RATE)
        pk = [(t, b"x") for t in range(100, 120)] + \
             [(t, b"x") for t in range(120 + gap, 140 + gap)]
        _, utt = voicemod.group_utterances(pk, self.RATE)
        assert len(utt) == 1

    def test_audio_offset_accumulates_packets_not_ticks(self):
        """
        The offset is a position in a file with the silence removed, so it must
        count packets. Using elapsed ticks would place every utterance after
        the first at the wrong point in the audio.
        """
        pk = [(t, b"x") for t in range(100, 120)] + \
             [(t, b"x") for t in range(9000, 9010)]
        _, utt = voicemod.group_utterances(pk, self.RATE)
        assert utt[0]["a"] == 0.0
        assert utt[1]["a"] == pytest.approx(20 * 0.010)   # 20 packets, not ticks

    def test_offsets_are_monotonic_and_packet_counts_sum(self):
        pk = []
        for block in range(6):
            base = block * 5000
            pk += [(base + t, b"x") for t in range(40)]
        ordered, utt = voicemod.group_utterances(pk, self.RATE)
        assert len(utt) == 6
        assert [u["a"] for u in utt] == sorted(u["a"] for u in utt)
        assert sum(u["n"] for u in utt) == len(ordered) == len(pk)

    def test_empty_input(self):
        assert voicemod.group_utterances([], self.RATE) == ([], [])


class TestSafeName:
    @pytest.mark.parametrize("raw,expected", [
        ("pavloven", "pavloven"),
        ("Albyyy-", "Albyyy-"),
        ("iTrix_", "iTrix"),            # trailing punctuation is stripped
        ("../../etc/passwd", "etc_passwd"),
        ("a/b\\c", "a_b_c"),
        ("", "unknown"),
        (None, "unknown"),
        ("...", "unknown"),
    ])
    def test_player_names_are_safe_as_filenames(self, raw, expected):
        """Names come from the demo and are arbitrary user input."""
        assert voicemod.safe_name(raw) == expected

    def test_no_separators_survive(self):
        for bad in ("a/b", "a\\b", "a:b", "a\x00b"):
            out = voicemod.safe_name(bad)
            assert "/" not in out and "\\" not in out and "\x00" not in out

    def test_traversal_cannot_escape_the_output_directory(self):
        for bad in ("../../etc/passwd", "..\\..\\windows\\system32", "./../x"):
            assert ".." not in voicemod.safe_name(bad)


class TestFilenameCollisions:
    """
    safe_name() is lossy, so different players can reduce to the same file.
    CS2 names are not unique either. Silently overwriting one player's comms
    with another's is the worst possible failure here: it looks like it worked.
    """

    def test_distinct_names_keep_their_own_files(self):
        taken = set()
        a = voicemod.unique_filename("pavloven", "76561199096127770", taken); taken.add(a)
        b = voicemod.unique_filename("RENAT4IK", "76561198910739052", taken); taken.add(b)
        assert (a, b) == ("pavloven.opus", "RENAT4IK.opus")

    def test_names_that_reduce_to_the_same_stem_are_disambiguated(self):
        taken = set()
        a = voicemod.unique_filename("iTrix", "76561198340681205", taken); taken.add(a)
        b = voicemod.unique_filename("iTrix_", "76561198111111111", taken); taken.add(b)
        assert a == "iTrix.opus"
        assert b != a and b.endswith(".opus") and "1111" in b

    def test_identical_names_are_disambiguated(self):
        taken = set()
        a = voicemod.unique_filename("bot", "76561190000001234", taken); taken.add(a)
        b = voicemod.unique_filename("bot", "76561190000005678", taken); taken.add(b)
        c = voicemod.unique_filename("bot", "76561190000009999", taken); taken.add(c)
        assert len({a, b, c}) == 3

    def test_same_steamid_suffix_still_resolves(self):
        """Two steamids ending in the same four digits must not collide."""
        taken = set()
        a = voicemod.unique_filename("x", "76561190000001234", taken); taken.add(a)
        b = voicemod.unique_filename("x", "76561199999991234", taken); taken.add(b)
        c = voicemod.unique_filename("x", "76561198888881234", taken); taken.add(c)
        assert len({a, b, c}) == 3
