"""Tests for the eTree setlist parser — covers every format that bit us."""
from audiohelper.live_tagger import parse_etree_file, generate_etree_file


def _labels(show):
    return [(s.label, len(s.tracks)) for s in show.sets]


# ── Key-value header format ───────────────────────────────────────────────────

def test_kv_header_basic():
    show = parse_etree_file(
        "Artist: Phish\nDate: 1997-12-31\nVenue: Madison Square Garden\n"
        "Location: New York, NY\nSource: SBD > DAT > FLAC\n\n"
        "Set 1:\n1. Mike's Song\n2. Weekapaug Groove\n\nEncore:\n3. Tweezer Reprise\n")
    assert show.artist == "Phish"
    assert show.date == "1997-12-31"
    assert show.venue == "Madison Square Garden"
    assert show.location == "New York, NY"
    assert show.source == "SBD > DAT > FLAC"
    assert _labels(show) == [("Set 1", 2), ("Encore", 1)]
    assert len(show.all_tracks()) == 3


def test_kv_header_case_insensitive_keys():
    show = parse_etree_file("ARTIST: moe.\ndate: 2001-05-05\n\n1. St. Augustine\n")
    assert show.artist == "moe."
    assert show.date == "2001-05-05"


# ── Numbered positional, multi-disc (the Rolling Stones case) ─────────────────

def test_numbered_multidisc():
    txt = (
        "The Rolling Stones 1999 06 08 Shepherds Bush Empire, London, UK\n"
        "Some Release VGP-225\n\nNo lineage available.\n***********\n\n"
        "Disc 1:\n01. Shattered\n02. Respectable\n\n"
        "Disc 2:\n01. Brown Sugar\n02. Jumping Jack Flash\n")
    show = parse_etree_file(txt)
    assert show.artist == "The Rolling Stones"
    assert show.date == "1999-06-08"
    assert show.venue == "Shepherds Bush Empire"
    assert show.location == "London, UK"
    assert _labels(show) == [("Disc 1", 2), ("Disc 2", 2)]
    # Continuous global numbering across discs
    tracks = show.all_tracks()
    assert [t.global_index for t in tracks] == [1, 2, 3, 4]
    assert tracks[2].title == "Brown Sugar"


def test_cd_marker_normalized_to_disc():
    show = parse_etree_file("Band 2020-01-01 Venue\n\nCD 1:\n1. A\n\nCD 2:\n2. B\n")
    assert [s.label for s in show.sets] == ["Disc 1", "Disc 2"]


# ── Unnumbered setlist (the GLAF case) ────────────────────────────────────────

def test_unnumbered_setlist_with_lineup_terminator():
    txt = (
        "Grahame Lesh & Friends\n2026-03-21\nThe Fillmore\nSan Francisco, CA\n\n"
        "Set 1:\nTuning\nMississippi Half-Step\nCold Rain and Snow >\n\n"
        "Encore:\nDonor Rap\nTruckin'\n\n"
        "Grahame Lesh - Guitar, Bass, and Vocals\nNels Cline - Guitar\n"
        "Source: Schoeps MK22 > MixPre 6 II\n")
    show = parse_etree_file(txt)
    assert show.artist == "Grahame Lesh & Friends"
    assert show.date == "2026-03-21"
    assert show.venue == "The Fillmore"
    assert show.location == "San Francisco, CA"
    assert _labels(show) == [("Set 1", 3), ("Encore", 2)]
    # Lineup line must NOT be captured as a track
    titles = [t.title for t in show.all_tracks()]
    assert "Grahame Lesh - Guitar, Bass, and Vocals" not in titles
    assert titles[-1] == "Truckin'"
    # Trailing source is folded into source
    assert "Schoeps MK22" in show.source


def test_unnumbered_does_not_eat_divider():
    show = parse_etree_file("Band\n2020-01-01\nVenue\n\nSet 1:\nSong One\nSong Two\n")
    assert [t.title for t in show.all_tracks()] == ["Song One", "Song Two"]


# ── Track comments in parentheses ─────────────────────────────────────────────

def test_track_comment_extracted():
    show = parse_etree_file("Artist: X\n\nSet 1:\n1. Eyes of the World (with jam)\n")
    t = show.all_tracks()[0]
    assert t.title == "Eyes of the World"
    assert t.comment == "with jam"


# ── Round-trip ────────────────────────────────────────────────────────────────

def test_roundtrip_preserves_tracks():
    original = parse_etree_file(
        "Artist: Phish\nDate: 1997-12-31\nVenue: MSG\n\n"
        "Set 1:\n1. Mike's Song\n2. Weekapaug\n")
    regenerated = generate_etree_file(original)
    reparsed = parse_etree_file(regenerated)
    assert reparsed.artist == original.artist
    assert [t.title for t in reparsed.all_tracks()] == \
           [t.title for t in original.all_tracks()]


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_input():
    show = parse_etree_file("")
    assert show.artist == ""
    assert show.all_tracks() == []


def test_bom_stripped():
    show = parse_etree_file("﻿Artist: Wilco\n\n1. Misunderstood\n")
    assert show.artist == "Wilco"
