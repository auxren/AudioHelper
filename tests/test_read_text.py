"""Tests for encoding detection and ripper-log rejection."""
from audiohelper.live_tagger import read_text_smart, looks_like_log_file


def test_utf8_plain(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("Café del Mar — smart ’quotes’".encode("utf-8"))
    assert "Café del Mar" in read_text_smart(p)
    assert "’quotes’" in read_text_smart(p)


def test_cp1252_smart_quotes(tmp_path):
    p = tmp_path / "b.txt"
    # 0x92 = Windows-1252 right single quote; 0x96 = en dash
    p.write_bytes(b"It\x92s Only Rock\x92n Roll \x96 live")
    out = read_text_smart(p)
    assert "It’s Only Rock’n Roll" in out
    assert "�" not in out  # no replacement chars


def test_utf16_le_bom(tmp_path):
    p = tmp_path / "c.txt"
    p.write_bytes(b"\xff\xfe" + "Grateful Dead\nVeneta, OR".encode("utf-16-le"))
    out = read_text_smart(p)
    assert out.startswith("Grateful Dead")
    assert "ÿþ" not in out  # BOM must not leak into the text


def test_utf16_be_bom(tmp_path):
    p = tmp_path / "d.txt"
    p.write_bytes(b"\xfe\xff" + "Phish".encode("utf-16-be"))
    assert read_text_smart(p).strip() == "Phish"


def test_utf8_bom(tmp_path):
    p = tmp_path / "e.txt"
    p.write_bytes(b"\xef\xbb\xbf" + "Wilco".encode("utf-8"))
    assert read_text_smart(p).strip() == "Wilco"


# ── Log-file detection ────────────────────────────────────────────────────────

def test_detects_eac_log():
    assert looks_like_log_file(
        "Exact Audio Copy V1.0 from 23. August 2011\n\nEAC extraction logfile")


def test_detects_foobar_log():
    assert looks_like_log_file("foobar2000 1.1.10 / Dynamic Range Meter")


def test_detects_xld_log():
    assert looks_like_log_file("X Lossless Decoder version 20250302\nUsed Drive : ...")


def test_detects_playlist_export():
    assert looks_like_log_file("11 tracks in playlist, average track length: 4:10")


def test_detects_ezcd():
    assert looks_like_log_file("EZ CD Audio Converter\nLog creation date: 17 Oct 2014")


def test_real_setlist_not_a_log():
    assert not looks_like_log_file(
        "Grateful Dead\nVeneta, OR\n\nSet 1:\nPlaying in the Band\nBird Song")
