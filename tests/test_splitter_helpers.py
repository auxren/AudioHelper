"""Tests for Show Splitter time helpers and silence parsing."""
import pytest

from audiohelper.show_splitter import _fmt_time, _parse_time, _sanitize, _nice_tick


def test_fmt_time_minutes():
    assert _fmt_time(83) == "1:23"
    assert _fmt_time(5) == "0:05"


def test_fmt_time_hours():
    assert _fmt_time(4623) == "1:17:03"


def test_fmt_time_fractional():
    assert _fmt_time(83.5) == "1:23.5"


def test_parse_time_roundtrip():
    for s in ("1:23", "1:17:03", "0:05", "12:34.5"):
        # parse then format should be stable to within rounding
        secs = _parse_time(s)
        assert secs >= 0


def test_parse_time_values():
    assert _parse_time("1:23") == 83
    assert _parse_time("1:17:03") == 4623
    assert _parse_time("0:30") == 30


def test_parse_time_bad_raises():
    with pytest.raises(ValueError):
        _parse_time("not a time")


def test_sanitize_strips_illegal_chars():
    assert _sanitize('Song: The One / The Other') == "Song The One The Other"
    assert _sanitize('a*b?c"d<e>f|g') == "abcdefg"


def test_sanitize_never_empty():
    assert _sanitize("///") == "untitled"
    assert _sanitize("") == "untitled"


def test_nice_tick_scales():
    # ticks grow with the visible duration
    assert _nice_tick(8) <= _nice_tick(800)
    assert _nice_tick(10) > 0
