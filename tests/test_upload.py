"""
Tests for accepting a .dem from the browser.

The filename arrives over the wire and becomes a path on disk. That is the
entire security surface of this feature, so most of these are about names that
are trying to be somewhere else.

safe_stem REBUILDS rather than escapes: everything outside [A-Za-z0-9_-] is
dropped, so separators, dots, colons and drive letters all go under one rule
instead of a blocklist that has to anticipate each of them. A name that
survives could not have been a path in the first place.

The rest is about partial uploads. A truncated 350 MB demo that gets kept
parses into a partial match or crashes somewhere inside awpy, and neither
should be mistaken for success.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import upload as up  # noqa: E402


class TestSafeStem:
    @pytest.mark.parametrize("raw", [
        "../../../../etc/passwd",
        "..\\..\\Windows\\System32\\config",
        "C:\\Users\\someone\\evil.dem",
        "/absolute/path.dem",
        "....",
        "..",
        "foo/../../bar",
        "%2e%2e%2fetc%2fpasswd",
        "a;rm -rf /.dem",
    ])
    def test_nothing_that_could_be_a_path_survives(self, raw):
        out = up.safe_stem(raw)
        assert not any(c in out for c in "/\\:"), out
        assert ".." not in out, out
        assert not out.startswith("-"), out
        assert Path(out).name == out, out

    def test_the_result_stays_inside_the_demo_directory(self, tmp_path):
        """
        The property that actually matters, stated as a path check rather than
        a character one: whatever comes out, joining it to the demo directory
        must not escape that directory.
        """
        for raw in ["../../x", "..\\..\\x", "/etc/passwd", "C:/Windows/x",
                    "....//....//x"]:
            dest = (tmp_path / f"{up.safe_stem(raw)}.dem").resolve()
            assert tmp_path.resolve() in dest.parents, raw

    def test_an_ordinary_name_is_left_recognisable(self):
        """Safety is not an excuse for turning 2v5.dem into a hash."""
        assert up.safe_stem("2v5.dem") == "2v5"
        assert up.safe_stem("my match (1).dem") == "my_match_1"

    def test_the_dem_suffix_is_dropped_once_not_greedily(self):
        assert up.safe_stem("match.dem.dem") == "match_dem"

    def test_an_empty_name_falls_back(self):
        assert up.safe_stem("") == "demo"
        assert up.safe_stem("   ") == "demo"
        assert up.safe_stem("///") == "demo"

    def test_it_is_length_capped(self):
        assert len(up.safe_stem("a" * 500)) <= 64

    def test_windows_device_names_are_not_returned_bare(self):
        """
        nul.dem writes a real file on this machine (Win 11, Python 3.13), so
        this guards builds where it does not rather than an observed failure.
        Where it bites, a 350 MB upload goes to the null device and the parse
        then sees nothing.
        """
        for name in ("nul", "CON", "aux", "com1", "LPT9"):
            assert up.safe_stem(name).lower() not in up.RESERVED

    def test_unicode_names_do_not_crash_and_stay_safe(self):
        out = up.safe_stem("\u0434\u0435\u043c\u043e.dem")
        assert out and not any(c in out for c in "/\\:")


class TestUniqueStem:
    def test_a_second_upload_of_the_same_name_does_not_overwrite(self, tmp_path,
                                                                 monkeypatch):
        """
        Silently replacing the first demo would also silently change what
        ?match=<name> resolves to, which is a confusing way to lose a review.
        """
        monkeypatch.setattr(up, "DEMO_DIR", tmp_path)
        assert up.unique_stem("m") == "m"
        (tmp_path / "m.dem").write_bytes(b"x")
        assert up.unique_stem("m") == "m_2"
        (tmp_path / "m_2.dem").write_bytes(b"x")
        assert up.unique_stem("m") == "m_3"


class FakeBody:
    """
    A request body handing out bytes in chunks, the way rfile does.

    `chunk_size` is capped well below upload.CHUNK on purpose. Without it the
    whole 4 KB test body arrives in a single read, `stall_after` never fires,
    and the truncation tests silently assert nothing - which is exactly what
    the first version of this class did.
    """

    def __init__(self, data, stall_after=None, chunk_size=64):
        self.data, self.pos = data, 0
        self.stall_after, self.chunk_size = stall_after, chunk_size

    def read(self, n):
        if self.stall_after is not None and self.pos >= self.stall_after:
            return b""                      # client vanished mid-upload
        chunk = self.data[self.pos:self.pos + min(n, self.chunk_size)]
        self.pos += len(chunk)
        return chunk


DEMO = up.DEMO_MAGIC + b"\x00" * 4096


class TestReceive:
    def test_a_demo_is_written_whole(self, tmp_path):
        dest = tmp_path / "m.dem"
        ok, err = up.receive(FakeBody(DEMO), len(DEMO), dest)
        assert ok and err is None
        assert dest.read_bytes() == DEMO

    def test_a_file_that_is_not_a_demo_is_refused_on_the_first_chunk(self, tmp_path):
        """
        Checked up front rather than after the upload and a 20-second parse,
        which would surface as a crash from inside awpy instead of the truth.
        """
        dest = tmp_path / "m.dem"
        body = b"\xff\xd8\xff\xe0JFIF" + b"\x00" * 4096
        ok, err = up.receive(FakeBody(body), len(body), dest)
        assert not ok and "not a CS2 demo" in err
        assert not dest.exists()

    def test_the_magic_matches_a_real_cs2_demo(self):
        """
        DEMO_MAGIC was written from memory as HL2DEMO - the Source 1 / CS:GO
        value - and silently rejected every genuine CS2 demo with "that is not
        a demo". CS2 is Source 2: PBDEMS2.

        Pinned against a real file rather than a constant copied from the same
        memory, because a magic number is exactly the kind of thing that looks
        right and is not.
        """
        demo = ROOT / "2v5.dem"
        if not demo.exists():
            pytest.skip("no demo on this machine")
        with demo.open("rb") as fh:
            assert fh.read(len(up.DEMO_MAGIC)) == up.DEMO_MAGIC

    def test_a_csgo_demo_is_told_what_it_is(self, tmp_path):
        """"Not a demo" is both wrong and unhelpful when it plainly is one."""
        dest = tmp_path / "m.dem"
        body = up.SOURCE1_MAGIC + bytes(4096)
        ok, err = up.receive(FakeBody(body), len(body), dest)
        assert not ok and "CS:GO" in err
        assert not dest.exists()

    def test_an_upload_that_ends_early_is_not_kept(self, tmp_path):
        """
        A truncated demo parses into a partial match or crashes. Neither should
        look like success, and the half-file must not be left where a later run
        would treat it as a demo.
        """
        dest = tmp_path / "m.dem"
        ok, err = up.receive(FakeBody(DEMO, stall_after=100), len(DEMO), dest)
        assert not ok and "early" in err
        assert not dest.exists(), "a partial demo was left behind"

    def test_an_oversized_declaration_is_refused_before_anything_is_written(
            self, tmp_path):
        dest = tmp_path / "m.dem"
        ok, err = up.receive(FakeBody(DEMO), 99 * 1024 ** 3, dest)
        assert not ok and "too large" in err
        assert not dest.exists()

    def test_an_empty_upload_is_refused(self, tmp_path):
        dest = tmp_path / "m.dem"
        ok, err = up.receive(FakeBody(b""), 0, dest)
        assert not ok
        assert not dest.exists()

    def test_no_part_file_survives_a_failure(self, tmp_path):
        dest = tmp_path / "m.dem"
        up.receive(FakeBody(DEMO, stall_after=100), len(DEMO), dest)
        assert list(tmp_path.glob("*")) == [], \
            "a .part file survived a failed upload"


class TestPipelineReuse:
    def test_the_server_shells_out_to_review_rather_than_reimplementing_it(self):
        """
        Two definitions of what a demo needs would drift, and the one that
        drifted would be the one nobody runs by hand.
        """
        src = (ROOT / "server" / "upload.py").read_text(encoding="utf-8")
        assert "review.py" in src
        # PARSING specifically. reanalyse() calls analyze.py and career.py on
        # purpose: re-deriving the review for a different player is a ~1.5s
        # operation review.py cannot express, because review.py always starts
        # from a demo and would re-parse 354 MB to redo 47 KB.
        # Matched as an INVOCATION - the `/ "name.py"` path construction - not
        # as a mention. Both scripts are discussed in the module docstring, and
        # a substring search flags that prose as a call.
        for stage in ("parse_demo.py", "voice.py"):
            assert f'/ "{stage}"' not in src, \
                f"upload.py invokes {stage} directly instead of through review.py"

    def test_review_emits_machine_readable_stages(self):
        src = (ROOT / "review.py").read_text(encoding="utf-8")
        assert "--progress" in src and '"@@"' in src


class TestOneAtATime:
    """
    The slot is claimed before the body is read.

    Checked at job-thread start instead, two browsers both pass while they are
    still uploading: the lock stops the second PARSE, but only after the person
    has spent a minute pushing 350 MB up the wire to be told no.
    """

    def test_a_second_reservation_is_refused(self):
        assert up.reserve()
        try:
            assert not up.reserve(), "two uploads were allowed to start"
        finally:
            up.release()

    def test_releasing_frees_the_slot(self):
        assert up.reserve()
        up.release()
        assert up.reserve()
        up.release()

    def test_release_is_safe_when_nothing_is_held(self):
        """A failed upload releases; so does the job thread. Both may run."""
        up.release()
        assert up.reserve()
        up.release()

    def test_the_server_reserves_before_reading_the_body(self):
        """
        Source-checked: the ordering is the whole point and cannot be seen from
        the outside without racing two real 350 MB uploads.
        """
        src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        body = src[src.index("def _upload"):src.index("def _chat")]
        assert body.index("up.reserve()") < body.index("up.receive("), \
            "the slot is claimed after the upload, not before"
        assert "up.release()" in body, "a refused upload never frees the slot"


class TestErrorMessages:
    """
    A player who uploads a half-downloaded demo gets told what to do about it.
    "DemoParser.Exception: MalformedMessage" is the truth and tells them
    nothing, so the plain sentence leads and the raw text is kept on the end
    for whoever reads the bug report.
    """

    def test_a_corrupt_demo_says_what_to_do(self):
        out = up.explain("DemoParser.Exception: MalformedMessage")
        assert "downloaded" in out
        assert "MalformedMessage" in out, "the raw error was thrown away"

    def test_terminal_colour_codes_never_reach_the_page(self):
        """The parser colours its output; a browser shows the escape codes as junk."""
        out = up.explain("[1;35mDemoParser.Exception[0m: [35mMalformedMessage[0m")
        assert "" not in out
        assert "MalformedMessage" in out and "downloaded" in out

    def test_an_unrecognised_error_is_passed_through_not_swallowed(self):
        """
        Inventing a friendly message for an error nobody has seen hides the
        one detail that would identify it.
        """
        assert up.explain("some novel failure") == "some novel failure"

    def test_an_empty_error_still_says_something(self):
        assert up.explain("") == "the pipeline failed"
        assert up.explain(None) == "the pipeline failed"
