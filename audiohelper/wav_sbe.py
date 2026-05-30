"""Sector Boundary Error (SBE) detection and repair for CD-format WAV files.

A CD frame is 1/75 sec = 588 stereo 16-bit samples = 2352 bytes. WAV files that
will be burned to CD or split with cue sheets must have a data-chunk size that
is a multiple of 2352 bytes. SBE = the data length is not aligned.

This module is pure-Python — works on any standard RIFF WAVE file without
needing shntool (which is abandoned upstream)."""

import os
import struct
from pathlib import Path

CD_SAMPLE_RATE = 44100
CD_CHANNELS = 2
CD_BITS = 16
CD_FRAME_SAMPLES = 588
CD_FRAME_BYTES = CD_FRAME_SAMPLES * CD_CHANNELS * (CD_BITS // 8)  # = 2352


class WavParseError(ValueError):
    pass


def parse_wav_info(path: Path) -> dict:
    """Read RIFF WAVE chunks. Returns a dict with format and data-chunk info."""
    with open(path, "rb") as f:
        riff = f.read(12)
        if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise WavParseError("not a RIFF WAVE file")
        fmt: tuple | None = None
        data_offset: int | None = None
        data_size: int | None = None
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            ck_id = hdr[:4]
            ck_size = struct.unpack("<I", hdr[4:8])[0]
            if ck_id == b"fmt ":
                body = f.read(min(ck_size, 40))
                if len(body) >= 16:
                    audio_format, channels, sr, _byte_rate, _block_align, bits = \
                        struct.unpack("<HHIIHH", body[:16])
                    fmt = (audio_format, channels, sr, bits)
                # advance past any remaining fmt-chunk bytes + odd-byte pad
                rest = ck_size - len(body)
                if rest > 0:
                    f.seek(rest, 1)
                if ck_size % 2:
                    f.seek(1, 1)
            elif ck_id == b"data":
                data_offset = f.tell()
                data_size = ck_size
                break  # do not consume audio body
            else:
                f.seek(ck_size, 1)
                if ck_size % 2:
                    f.seek(1, 1)
        if fmt is None or data_offset is None or data_size is None:
            raise WavParseError("missing fmt or data chunk")
    return {
        "audio_format": fmt[0],  # 1 = PCM, 3 = IEEE float, 0xFFFE = WAVE_FORMAT_EXTENSIBLE
        "channels": fmt[1],
        "sample_rate": fmt[2],
        "bits": fmt[3],
        "data_offset": data_offset,
        "data_size": data_size,
        "file_size": path.stat().st_size,
    }


def is_cd_format(info: dict) -> bool:
    """True if the file matches Red Book CD audio parameters (PCM, 16/44.1/stereo)."""
    return (
        info["audio_format"] in (1, 0xFFFE)
        and info["sample_rate"] == CD_SAMPLE_RATE
        and info["channels"] == CD_CHANNELS
        and info["bits"] == CD_BITS
    )


def sbe_status(info: dict) -> tuple[str, int]:
    """Return (status, leftover_bytes).
    status: 'na' (not CD format) | 'ok' (aligned) | 'sbe' (misaligned)."""
    if not is_cd_format(info):
        return ("na", 0)
    leftover = info["data_size"] % CD_FRAME_BYTES
    return ("sbe", leftover) if leftover else ("ok", 0)


def pad_bytes_for(info: dict) -> int:
    """How many silence bytes we'd need to append to align."""
    leftover = info["data_size"] % CD_FRAME_BYTES
    return 0 if leftover == 0 else CD_FRAME_BYTES - leftover


def fix_sbe_to(src: Path, dst: Path, info: dict) -> int:
    """Write a sector-aligned copy of src to dst by post-pending silence inside
    the data chunk. Returns the number of silence bytes added."""
    status, leftover = sbe_status(info)
    if status != "sbe":
        raise ValueError(f"no SBE to fix (status={status})")
    pad = CD_FRAME_BYTES - leftover

    with open(src, "rb") as fin, open(dst, "wb") as fout:
        riff_hdr = fin.read(12)
        if len(riff_hdr) < 12:
            raise WavParseError("truncated RIFF header")
        new_riff_size = info["file_size"] + pad - 8
        fout.write(b"RIFF" + struct.pack("<I", new_riff_size) + b"WAVE")

        while True:
            hdr = fin.read(8)
            if not hdr:
                break
            if len(hdr) < 8:
                fout.write(hdr)
                break
            ck_id = hdr[:4]
            ck_size = struct.unpack("<I", hdr[4:8])[0]
            if ck_id == b"data":
                new_size = ck_size + pad
                fout.write(b"data" + struct.pack("<I", new_size))
                _stream(fin, fout, ck_size)
                fout.write(b"\x00" * pad)
                # Original odd-byte pad (if any) should be dropped because we
                # changed the size; ensure parity for the new size.
                if ck_size % 2:
                    fin.read(1)
                if new_size % 2:
                    fout.write(b"\x00")
            else:
                fout.write(hdr)
                _stream(fin, fout, ck_size + (1 if ck_size % 2 else 0))
    return pad


def fix_sbe_in_place(path: Path, info: dict) -> int:
    """Atomically rewrite `path` with sector-aligned padding. Returns silence bytes added."""
    tmp = path.with_suffix(path.suffix + ".sbe-tmp")
    try:
        pad = fix_sbe_to(path, tmp, info)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    return pad


def _stream(fin, fout, total: int, chunk: int = 1 << 20) -> None:
    remaining = total
    while remaining > 0:
        buf = fin.read(min(remaining, chunk))
        if not buf:
            return
        fout.write(buf)
        remaining -= len(buf)
