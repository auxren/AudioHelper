"""Low-level tag read/write via ffprobe + ffmpeg.

Tag writing uses ffmpeg with -c copy so it's a metadata-only rewrite
(no re-encoding). Output is written to a sibling temp file and then
atomically renamed over the original."""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional


# Canonical tag keys we manage in the GUI. We write these uppercase (Vorbis
# convention); ffmpeg normalizes per-container as needed.
STANDARD_TAGS = (
    "ARTIST", "ALBUM", "ALBUMARTIST", "TITLE", "DATE", "GENRE",
    "TRACKNUMBER", "TRACKTOTAL", "DISCNUMBER", "DISCTOTAL",
    "COMPOSER", "PERFORMER", "COMMENT",
    # Live-recording extensions (matching foo_tradersfriend / etree convention)
    "VENUE", "SOURCE", "NOTES",
)


def _basename_of(member: str) -> str:
    return member.replace("\\", "/").rsplit("/", 1)[-1]


def read_tags(path: Path, ffprobe_exe: Path) -> dict:
    """Return a normalized upper-case tag dict, or empty dict on failure."""
    try:
        r = subprocess.run(
            [str(ffprobe_exe), "-v", "error", "-show_format",
             "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return {}
    if r.returncode != 0:
        return {}
    try:
        j = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    # ffprobe puts tags under format.tags and per-stream tags. Take format-level first.
    raw: dict[str, str] = {}
    fmt = j.get("format") or {}
    for k, v in (fmt.get("tags") or {}).items():
        raw[k.upper()] = str(v)
    # Stream-level tags are sometimes where MP3 ID3 ends up
    for s in j.get("streams") or []:
        for k, v in (s.get("tags") or {}).items():
            raw.setdefault(k.upper(), str(v))
    # ffmpeg flattens some keys; provide aliases
    aliases = {
        "TRACK": "TRACKNUMBER",
        "ALBUM_ARTIST": "ALBUMARTIST",
        "DISC": "DISCNUMBER",
    }
    out: dict[str, str] = {}
    for k, v in raw.items():
        out[aliases.get(k, k)] = v
    return out


def has_embedded_cover(path: Path, ffprobe_exe: Path) -> bool:
    """True if the file has at least one attached_pic / cover image."""
    try:
        r = subprocess.run(
            [str(ffprobe_exe), "-v", "error", "-select_streams", "v",
             "-show_entries", "stream_disposition=attached_pic",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return False
    return "1" in (r.stdout or "")


def build_write_argv(
    ffmpeg_exe: Path,
    src: Path,
    dst: Path,
    tags: dict[str, str],
    cover: Optional[Path] = None,
    clear_cover: bool = False,
) -> list[str]:
    """Construct an ffmpeg argv that copies audio + writes tags + optionally
    replaces or clears the cover art. -c copy throughout, no re-encoding."""
    argv: list[str] = [str(ffmpeg_exe), "-y", "-hide_banner", "-i", str(src)]
    if cover is not None:
        argv += ["-i", str(cover)]

    argv += ["-map", "0:a"]
    if cover is not None:
        # Replace any existing picture with the new one
        argv += ["-map", "1:v", "-disposition:v:0", "attached_pic"]
    elif not clear_cover:
        # Preserve existing picture, if any
        argv += ["-map", "0:v?"]
    # else: drop existing picture by not mapping any video stream

    argv += ["-c", "copy", "-map_metadata", "0"]

    for key, value in tags.items():
        # ffmpeg interprets an empty value as "remove this tag".
        argv += ["-metadata", f"{key}={value}"]

    argv.append(str(dst))
    return argv


def apply_tags_in_place(
    ffmpeg_exe: Path,
    target: Path,
    tags: dict[str, str],
    cover: Optional[Path] = None,
    clear_cover: bool = False,
) -> None:
    """Run ffmpeg to a sibling temp file, then atomic-rename over `target`."""
    # Keep the original suffix so ffmpeg auto-detects the output muxer.
    tmp = target.with_name(target.stem + ".tagtmp" + target.suffix)
    argv = build_write_argv(ffmpeg_exe, target, tmp, tags, cover, clear_cover)
    try:
        r = subprocess.run(
            argv,
            capture_output=True, text=True, timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg launch failed: {e}") from e
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        err = (r.stderr or "").strip().splitlines()
        msg = " | ".join(s.strip() for s in err[-3:]) or f"exit {r.returncode}"
        raise RuntimeError(msg)
    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg produced no output")
    os.replace(tmp, target)
