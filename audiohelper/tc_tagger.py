"""Format-aware audio tag writer using mutagen.

Ported and simplified from TagCleaner (github.com/auxren/TagCleaner).
Handles FLAC, MP3, WAV, AIFF, M4A, Ogg, and Opus natively without
spawning ffmpeg — so large batches are fast and cover art is preserved.

Public API:
    write_tags(path, tags)   — write a dict of uppercase tag keys
    read_tags_mutagen(path)  — read tags as an uppercase dict
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    from mutagen.flac import FLAC
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TSO2, TSOP
    from mutagen.mp3 import MP3
    from mutagen import File as MutagenFile
    from mutagen.mp4 import MP4
    from mutagen.oggvorbis import OggVorbis
    from mutagen.oggopus import OggOpus
    from mutagen.wave import WAVE
    from mutagen.aiff import AIFF
    _MUTAGEN_OK = True
except ImportError:
    _MUTAGEN_OK = False


def mutagen_available() -> bool:
    return _MUTAGEN_OK


def write_tags(path: Path, tags: dict[str, str]) -> None:
    """Write *tags* (uppercase keys) to *path*. No re-encoding — metadata only.

    Raises RuntimeError if mutagen is not installed or the format is unsupported.
    Tags with empty string values are skipped (not deleted).
    """
    if not _MUTAGEN_OK:
        raise RuntimeError(
            "mutagen is not installed. Run:  pip install mutagen\n"
            "Or fall back to ffmpeg-based tagging."
        )
    tags = {k: v for k, v in tags.items() if v}  # drop empty values
    ext = path.suffix.lower()

    if ext == ".flac":
        _write_flac(path, tags)
    elif ext == ".mp3":
        _write_mp3(path, tags)
    elif ext in (".wav", ".wave"):
        _write_wav(path, tags)
    elif ext in (".aif", ".aiff"):
        _write_aiff(path, tags)
    elif ext in (".m4a", ".m4b", ".aac"):
        _write_m4a(path, tags)
    elif ext in (".ogg", ".oga", ".opus"):
        _write_ogg(path, tags)
    else:
        raise RuntimeError(f"Unsupported format for mutagen tagging: {ext}")


def read_tags_mutagen(path: Path) -> dict[str, str]:
    """Return an uppercase tag dict from *path*, or empty dict on failure."""
    if not _MUTAGEN_OK:
        return {}
    try:
        f = MutagenFile(str(path), easy=False)
        if f is None or f.tags is None:
            return {}
        raw: dict[str, str] = {}
        # FLAC / Ogg: dict-like with list values
        if hasattr(f, "tags") and hasattr(f.tags, "items"):
            for k, v in f.tags.items():
                val = v[0] if isinstance(v, (list, tuple)) and v else str(v)
                raw[k.upper()] = str(val)
        return raw
    except Exception:
        return {}


# ── FLAC ─────────────────────────────────────────────────────────────────────

def _write_flac(path: Path, tags: dict[str, str]) -> None:
    audio = FLAC(str(path))
    _apply_vorbis(audio, tags)
    audio.save()


def _apply_vorbis(audio, tags: dict[str, str]) -> None:
    """Write Vorbis comment tags (FLAC / Ogg style)."""
    _VORBIS_KEYS = {
        "ARTIST", "ALBUMARTIST", "ARTISTSORT", "ALBUMARTISTSORT",
        "ALBUM", "DATE", "TITLE", "TRACKNUMBER", "TRACKTOTAL", "TOTALTRACKS",
        "DISCNUMBER", "DISCTOTAL", "TOTALDISCS",
        "GENRE", "COMMENT", "VENUE", "LOCATION", "SOURCE", "NOTES", "PERFORMER",
        "COMPOSER",
    }
    sort_name = _sort_key(tags.get("ARTIST", "") or tags.get("ALBUMARTIST", ""))
    if "ARTIST" in tags:
        audio["ARTIST"] = tags["ARTIST"]
        audio["ALBUMARTIST"] = tags.get("ALBUMARTIST", tags["ARTIST"])
        if sort_name:
            audio["ARTISTSORT"] = sort_name
            audio["ALBUMARTISTSORT"] = sort_name
    for key in _VORBIS_KEYS - {"ARTIST", "ALBUMARTIST", "ARTISTSORT", "ALBUMARTISTSORT"}:
        if key in tags:
            audio[key] = tags[key]


# ── MP3 ──────────────────────────────────────────────────────────────────────

def _write_mp3(path: Path, tags: dict[str, str]) -> None:
    try:
        audio = EasyID3(str(path))
    except Exception:
        mp3 = MP3(str(path))
        mp3.add_tags()
        mp3.save()
        audio = EasyID3(str(path))

    sort_name = _sort_key(tags.get("ARTIST", "") or tags.get("ALBUMARTIST", ""))
    if "ARTIST" in tags:
        audio["artist"] = tags["ARTIST"]
        audio["albumartist"] = tags.get("ALBUMARTIST", tags["ARTIST"])
        if sort_name:
            audio["artistsort"] = sort_name
            audio["albumartistsort"] = sort_name
    _mp3_field(audio, "album", tags, "ALBUM")
    _mp3_field(audio, "date", tags, "DATE")
    _mp3_field(audio, "title", tags, "TITLE")
    _mp3_field(audio, "genre", tags, "GENRE")
    _mp3_field(audio, "comment", tags, "COMMENT")
    if "TRACKNUMBER" in tags:
        total = tags.get("TRACKTOTAL") or tags.get("TOTALTRACKS")
        audio["tracknumber"] = f"{tags['TRACKNUMBER']}/{total}" if total else tags["TRACKNUMBER"]
    if "DISCNUMBER" in tags:
        total = tags.get("DISCTOTAL") or tags.get("TOTALDISCS")
        audio["discnumber"] = f"{tags['DISCNUMBER']}/{total}" if total else tags["DISCNUMBER"]
    audio.save()


def _mp3_field(audio, easy_key: str, tags: dict, tag_key: str) -> None:
    if tag_key in tags:
        audio[easy_key] = tags[tag_key]


# ── WAV ───────────────────────────────────────────────────────────────────────

def _write_wav(path: Path, tags: dict[str, str]) -> None:
    audio = WAVE(str(path))
    if audio.tags is None:
        audio.add_tags()
    _write_id3_frames(audio.tags, tags)
    audio.save()


def _write_aiff(path: Path, tags: dict[str, str]) -> None:
    audio = AIFF(str(path))
    if audio.tags is None:
        audio.add_tags()
    _write_id3_frames(audio.tags, tags)
    audio.save()


def _write_id3_frames(id3, tags: dict[str, str]) -> None:
    sort_name = _sort_key(tags.get("ARTIST", "") or tags.get("ALBUMARTIST", ""))
    if "ARTIST" in tags:
        id3["TPE1"] = TPE1(encoding=3, text=tags["ARTIST"])
        id3["TPE2"] = TPE2(encoding=3, text=tags.get("ALBUMARTIST", tags["ARTIST"]))
        if sort_name:
            id3["TSOP"] = TSOP(encoding=3, text=sort_name)
            id3["TSO2"] = TSO2(encoding=3, text=sort_name)
    if "ALBUM" in tags:
        id3["TALB"] = TALB(encoding=3, text=tags["ALBUM"])
    if "DATE" in tags:
        id3["TDRC"] = TDRC(encoding=3, text=tags["DATE"])
    if "TITLE" in tags:
        id3["TIT2"] = TIT2(encoding=3, text=tags["TITLE"])
    if "TRACKNUMBER" in tags:
        total = tags.get("TRACKTOTAL") or tags.get("TOTALTRACKS")
        trk = f"{tags['TRACKNUMBER']}/{total}" if total else tags["TRACKNUMBER"]
        id3["TRCK"] = TRCK(encoding=3, text=trk)
    if "DISCNUMBER" in tags:
        total = tags.get("DISCTOTAL") or tags.get("TOTALDISCS")
        dsc = f"{tags['DISCNUMBER']}/{total}" if total else tags["DISCNUMBER"]
        id3["TPOS"] = TPOS(encoding=3, text=dsc)


# ── M4A ───────────────────────────────────────────────────────────────────────

_MP4_MAP = {
    "ARTIST":      "\xa9ART",
    "ALBUMARTIST": "aART",
    "ALBUM":       "\xa9alb",
    "DATE":        "\xa9day",
    "TITLE":       "\xa9nam",
    "COMMENT":     "\xa9cmt",
    "GENRE":       "\xa9gen",
}


def _write_m4a(path: Path, tags: dict[str, str]) -> None:
    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    sort_name = _sort_key(tags.get("ARTIST", "") or tags.get("ALBUMARTIST", ""))
    for our_key, atom in _MP4_MAP.items():
        if our_key in tags:
            audio.tags[atom] = [tags[our_key]]
    if "ARTIST" in tags:
        audio.tags["aART"] = [tags.get("ALBUMARTIST", tags["ARTIST"])]
        if sort_name:
            audio.tags["soar"] = [sort_name]
            audio.tags["soaa"] = [sort_name]
    if "TRACKNUMBER" in tags:
        try:
            t = int(tags["TRACKNUMBER"])
            tot = int(tags.get("TRACKTOTAL") or tags.get("TOTALTRACKS") or 0)
            audio.tags["trkn"] = [(t, tot)]
        except ValueError:
            pass
    if "DISCNUMBER" in tags:
        try:
            d = int(tags["DISCNUMBER"])
            dtot = int(tags.get("DISCTOTAL") or tags.get("TOTALDISCS") or 0)
            audio.tags["disk"] = [(d, dtot)]
        except ValueError:
            pass
    audio.save()


# ── Ogg / Opus ────────────────────────────────────────────────────────────────

def _write_ogg(path: Path, tags: dict[str, str]) -> None:
    audio = MutagenFile(str(path))
    if audio is None or not isinstance(audio, (OggVorbis, OggOpus)):
        raise RuntimeError(f"Unrecognised Ogg variant: {path}")
    _apply_vorbis(audio, tags)
    audio.save()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sort_key(name: str) -> str:
    return re.sub(r"^[Tt]he\s+", "", name or "").strip()
