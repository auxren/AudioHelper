"""Tests for Bulk Tag Cleanup metadata inference."""
from pathlib import Path

from audiohelper.bulk_tagger import (
    parse_date_from, artist_from_folder, city_region_from_folder,
    _is_junk_artist, scan_library, infer_concert, ConcertFolder,
)


# ── Date parsing ──────────────────────────────────────────────────────────────

def test_iso_date():
    assert parse_date_from("Phish 1997-12-31 MSG") == "1997-12-31"


def test_compact_date():
    assert parse_date_from("gd19770508sbd") == "1977-05-08"


def test_us_full_date():
    assert parse_date_from("show 06/08/1999 london") == "1999-06-08"


def test_dotted_date():
    assert parse_date_from("1984.09.21 venue") == "1984-09-21"


def test_no_date_returns_none():
    assert parse_date_from("Remain in Light Tour") is None
    assert parse_date_from("Greek Theatre, Berkeley CA") is None


def test_invalid_date_rejected():
    assert parse_date_from("2020-13-45") is None  # impossible month/day


# ── Artist from folder ────────────────────────────────────────────────────────

def test_etree_prefix():
    assert artist_from_folder("gd770508") == "Grateful Dead"
    assert artist_from_folder("ph971231") == "Phish"


def test_artist_before_date():
    assert artist_from_folder("Talking Heads 1980-08-27 Wollman") == "Talking Heads"


def test_date_dash_artist_dash_venue():
    assert artist_from_folder("1980-08-27 - Talking Heads - Wollman") == "Talking Heads"


def test_no_artist_when_no_date_boundary():
    assert artist_from_folder("RandomFolderName") is None


# ── City / region ─────────────────────────────────────────────────────────────

def test_city_state():
    city, region = city_region_from_folder("Venue, San Francisco, CA")
    assert city == "San Francisco" and region == "CA"


def test_city_country():
    city, region = city_region_from_folder("Empire, London, UK")
    assert city == "London" and region == "UK"


# ── Junk artist detection ─────────────────────────────────────────────────────

def test_junk_artist():
    assert _is_junk_artist("ÿþ--------------------")
    assert _is_junk_artist("25569")
    assert _is_junk_artist("Playlist length: 45 minutes")
    assert _is_junk_artist("EZ CD Audio Converter")
    assert _is_junk_artist("")


def test_real_artist_not_junk():
    assert not _is_junk_artist("The Rolling Stones")
    assert not _is_junk_artist("moe.")
    assert not _is_junk_artist("KGB")
    assert not _is_junk_artist("Sound Tribe Sector 9")
    assert not _is_junk_artist("Grahame Lesh & Friends")


def test_pure_numeric_artist_is_junk_known_tradeoff():
    # Pure-digit strings are rejected so years / "25569" don't become artists.
    # The cost: an all-numeric band name like "311" is also flagged — rare,
    # and the user can fix it via the editable Proposed Artist cell.
    assert _is_junk_artist("311")
    assert _is_junk_artist("1995")


# ── Album name construction ───────────────────────────────────────────────────

def test_album_name_full():
    c = ConcertFolder(folder=Path("/x"), date="2026-03-21", venue="The Fillmore",
                      city="San Francisco", region="CA", source="Schoeps MK22")
    assert c.album_name() == "2026-03-21 The Fillmore, San Francisco, CA [Schoeps MK22]"


def test_album_override_wins():
    c = ConcertFolder(folder=Path("/x"), date="2026-03-21", venue="The Fillmore",
                      album_override="My Custom Album")
    assert c.album_name() == "My Custom Album"


def test_confidence_scoring():
    c = ConcertFolder(folder=Path("/x"), artist="Phish", date="1997-12-31",
                      venue="MSG", city="NYC",
                      audio_files=[Path("a.flac")], titles=["Song"])
    assert c.confidence() == 1.0  # all fields + track/file match


# ── Disc-folder merging (uses a temp tree) ────────────────────────────────────

def test_scan_merges_cd_subfolders(tmp_path):
    root = tmp_path / "Band 2020-01-01 Venue"
    (root / "CD1").mkdir(parents=True)
    (root / "CD2").mkdir(parents=True)
    for i in range(1, 4):
        (root / "CD1" / f"{i:02d} Track.flac").write_bytes(b"x")
    for i in range(1, 3):
        (root / "CD2" / f"{i:02d} Track.flac").write_bytes(b"x")
    concerts = scan_library(tmp_path)
    assert len(concerts) == 1                  # merged, not 2 separate
    c = concerts[0]
    assert c.folder == root                    # rooted at the show folder
    assert len(c.audio_files) == 5             # 3 + 2
    assert c.discs == [1, 1, 1, 2, 2]          # disc numbers from CD1/CD2


def test_scan_single_folder(tmp_path):
    show = tmp_path / "gd770508"
    show.mkdir()
    (show / "01 Bertha.flac").write_bytes(b"x")
    (show / "02 Jack Straw.flac").write_bytes(b"x")
    concerts = scan_library(tmp_path)
    assert len(concerts) == 1
    assert len(concerts[0].audio_files) == 2
    # discs stub empty for single-folder shows
    assert concerts[0].discs == []
