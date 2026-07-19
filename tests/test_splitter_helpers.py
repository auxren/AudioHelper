"""Tests for Show Splitter time helpers and silence parsing."""
import pytest

from audiohelper.show_splitter import (
    _fmt_time, _parse_time, _sanitize, _nice_tick,
    _apply_name_template, _guess_abbrev,
    serialize_session, parse_session,
)


def test_session_roundtrip():
    meta = {"artist": "ALO", "date": "2026-05-24", "venue": "HopMonk",
            "location": "Novato, CA", "abbrev": "alo",
            "source_chain": "Schoeps MK22 > MixPre", "format": "FLAC",
            "template": "%a%dd%Dt%n"}
    tracks = [(0.0, 1, "ANIMAL"), (263.5, 1, "BLANK"), (540.2, 2, "GTDIA")]
    text = serialize_session("/x/alo.caf", meta, tracks)
    d = parse_session(text)
    assert d["meta"]["artist"] == "ALO"
    assert d["meta"]["template"] == "%a%dd%Dt%n"
    assert d["meta"]["source"] == "/x/alo.caf"
    assert d["tracks"] == tracks


def test_session_tolerates_hand_edits():
    # Comments, blank lines, and a pipe in a title must survive.
    text = (
        "# my edited session\n"
        "artist = Phish\n"
        "date = 1997-12-31\n"
        "\n"
        "[tracks]\n"
        "0:00 | 1 | Mike's Song\n"
        "5:30 | 1 | I Am Hydrogen | reprise\n"
    )
    d = parse_session(text)
    assert d["meta"]["artist"] == "Phish"
    assert d["tracks"][0] == (0.0, 1, "Mike's Song")
    assert d["tracks"][1] == (330.0, 1, "I Am Hydrogen | reprise")


def test_etree_filename_template():
    assert _apply_name_template("%a%dd%Dt%n", "bruce", "2026-04-13",
                                1, 1, "Rosalita", ".flac") == "bruce2026-04-13d1t01.flac"
    assert _apply_name_template("%a%dd%Dt%n", "bruce", "2026-04-13",
                                2, 3, "X", ".flac") == "bruce2026-04-13d2t03.flac"


def test_filename_template_with_title():
    assert _apply_name_template("%D-%n %t", "x", "2020-01-01",
                                1, 5, "Maria", ".mp3") == "1-05 Maria.mp3"


def test_filename_template_adds_extension_once():
    out = _apply_name_template("%a%dd%Dt%n", "x", "2020-01-01", 1, 1, "t", ".flac")
    assert out.endswith(".flac") and out.count(".flac") == 1


def test_guess_abbrev():
    assert _guess_abbrev("Grateful Dead") == "gd"
    assert _guess_abbrev("Bruce Springsteen") == "bruce"
    assert _guess_abbrev("ALO") == "alo"
    assert _guess_abbrev("Grahame Lesh & Friends") == "glaf"
    assert _guess_abbrev("") == ""


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


def test_envelope_silence_detection():
    """detect_quiet_boundaries finds the gaps between 'songs' from the envelope.
    Skips if tkinter can't create a root (headless CI without a display)."""
    import math
    import pytest
    try:
        import tkinter as tk
        from audiohelper import theme as _t
        root = tk.Tk()
    except Exception:
        pytest.skip("no display / tkinter root")
    try:
        _t.apply(root)
        root.withdraw()
        from audiohelper.show_splitter import WaveformView
        wv = WaveformView(root)
        dur, sps = 300.0, 100
        n = int(dur * sps)
        wv._duration = dur
        env = []
        for i in range(n):
            t = i / sps
            env.append(0.01 if (90 < t < 95 or 185 < t < 190)
                       else abs(0.5 + 0.3 * math.sin(t * 5)))
        wv._samples = env
        b = wv.detect_quiet_boundaries(min_gap=25)
        # Two resume points, near 95 s and 190 s
        assert len(b) == 2
        assert abs(b[0] - 95) < 3
        assert abs(b[1] - 190) < 3
    finally:
        root.destroy()


# ── Audacity label parsing ─────────────────────────────────────────────────────

def test_audacity_labels_basic():
    from audiohelper.show_splitter import parse_audacity_labels
    text = (
        "0.000000\t315.864998\tCan't Wait For Tonight\n"
        "315.864998\t855.487600\tThrow It Away\n"
        "855.487600\t1543.216772\tNicolette >\n"
    )
    rows = parse_audacity_labels(text)
    assert len(rows) == 3
    assert rows[0] == (0.0, 315.864998, "Can't Wait For Tonight")
    assert rows[2][2] == "Nicolette >"


def test_audacity_labels_sorted_and_untitled():
    from audiohelper.show_splitter import parse_audacity_labels
    # Out-of-order rows and a label with no title.
    text = "100.5\t200\tSecond\n0\t100.5\n"
    rows = parse_audacity_labels(text)
    assert [r[0] for r in rows] == [0.0, 100.5]
    assert rows[0][2] == ""


def test_audacity_labels_skips_spectral_rows():
    from audiohelper.show_splitter import parse_audacity_labels
    text = "0\t10\tIntro\n\\\t440.0\t880.0\n10\t20\tJam\n"
    assert len(parse_audacity_labels(text)) == 2


def test_audacity_labels_rejects_setlists():
    from audiohelper.show_splitter import parse_audacity_labels
    # Ordinary eTree text must not be mistaken for labels.
    assert parse_audacity_labels("Artist: Phish\n1. Tweezer\n2. Hood\n") == []
    assert parse_audacity_labels("") == []
