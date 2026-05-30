"""Integration tests for tag read/write and FLAC cover preservation.

Skip automatically when mutagen or ffmpeg aren't available.
"""
import subprocess

import pytest

from tests.conftest import HAVE_FFMPEG, HAVE_MUTAGEN

pytestmark = pytest.mark.skipif(
    not (HAVE_FFMPEG and HAVE_MUTAGEN),
    reason="needs ffmpeg + mutagen")


def test_write_then_read_flac(synth_flac):
    from audiohelper.tc_tagger import write_tags, read_tags_mutagen
    write_tags(synth_flac, {
        "ARTIST": "Phish", "ALBUM": "1997-12-31 MSG",
        "TITLE": "Tweezer", "DATE": "1997-12-31",
        "TRACKNUMBER": "07", "VENUE": "Madison Square Garden",
    })
    tags = read_tags_mutagen(synth_flac)
    assert tags.get("ARTIST") == "Phish"
    assert tags.get("TITLE") == "Tweezer"
    assert tags.get("VENUE") == "Madison Square Garden"
    # ALBUMARTIST auto-filled from ARTIST
    assert tags.get("ALBUMARTIST") == "Phish"


def test_write_tags_preserves_flac_picture(tmp_path, synth_flac):
    """The exact bug from the GLAF files: writing tags must NOT drop an
    embedded FLAC PICTURE block."""
    from mutagen.flac import FLAC, Picture
    from audiohelper.tc_tagger import write_tags

    # Embed a tiny cover picture
    pic = Picture()
    pic.type = 3  # front cover
    pic.mime = "image/jpeg"
    pic.data = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"  # minimal JPEG-ish
    f = FLAC(str(synth_flac))
    f.add_picture(pic)
    f.save()
    assert len(FLAC(str(synth_flac)).pictures) == 1

    # Now write tags — picture must survive
    write_tags(synth_flac, {"ARTIST": "Grahame Lesh & Friends", "TITLE": "Tuning"})
    after = FLAC(str(synth_flac))
    assert len(after.pictures) == 1
    assert after.pictures[0].type == 3
    assert after["ARTIST"][0] == "Grahame Lesh & Friends"


def test_write_then_read_mp3(tmp_path):
    from audiohelper.tc_tagger import write_tags
    from mutagen.easyid3 import EasyID3
    mp3 = tmp_path / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1",
         "-c:a", "libmp3lame", str(mp3)], capture_output=True, check=True)
    write_tags(mp3, {"ARTIST": "moe.", "TITLE": "Rebubula", "TRACKNUMBER": "3"})
    tags = EasyID3(str(mp3))
    assert tags["artist"][0] == "moe."
    assert tags["title"][0] == "Rebubula"


def test_ffprobe_reads_tags(synth_flac):
    """tag_io.read_tags (ffprobe path) round-trips with mutagen writes."""
    from audiohelper.tc_tagger import write_tags
    from audiohelper import tag_io
    from audiohelper.tools import get_tool
    from audiohelper.config import Config

    write_tags(synth_flac, {"ARTIST": "Wilco", "ALBUM": "YHF"})
    cfg = Config()
    ffprobe = get_tool("ffprobe").path(cfg)
    if not ffprobe.exists():
        pytest.skip("ffprobe not resolved")
    tags = tag_io.read_tags(synth_flac, ffprobe)
    assert tags.get("ARTIST") == "Wilco"
