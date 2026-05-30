"""Pure-Python DSDIFF (DFF) file merger.

Handles uncompressed DSD only ('DSD ' compression type — the common case).
DST-compressed DSDIFF is rejected with a clear error; use TASCAM Hi-Res Editor
for those files.

The merge streams audio data in 1 MiB chunks so even multi-gigabyte concert
recordings are handled without loading everything into RAM.
"""

import struct
from pathlib import Path

_HDR  = 12     # 4-byte ID + 8-byte big-endian size
_DSDIFF_VERSION = 0x01050000   # 1.5.0.0


# ── low-level helpers ─────────────────────────────────────────────────────

def _u64(data: bytes, off: int = 0) -> int:
    return struct.unpack_from(">Q", data, off)[0]


def _u32(data: bytes, off: int = 0) -> int:
    return struct.unpack_from(">I", data, off)[0]


def _u16(data: bytes, off: int = 0) -> int:
    return struct.unpack_from(">H", data, off)[0]


def _hdr(chunk_id: bytes, size: int) -> bytes:
    return chunk_id + struct.pack(">Q", size)


def _chunk(chunk_id: bytes, data: bytes) -> bytes:
    """Pack a complete IFF chunk including alignment pad byte."""
    raw = _hdr(chunk_id, len(data)) + data
    if len(data) % 2:
        raw += b"\x00"
    return raw


# ── DFF reader ────────────────────────────────────────────────────────────

class _DffFile:
    """Parses a DFF file's headers and locates the DSD audio block."""

    def __init__(self, path: Path) -> None:
        self.path        = path
        self.prop_bytes  = b""   # raw PROP chunk data (starts with "SND " form type)
        self.dsd_offset  = 0     # byte offset in file where DSD audio data begins
        self.dsd_size    = 0     # number of DSD audio bytes
        self.sample_rate = 0
        self.channels    = 0
        self._parse()

    def _parse(self) -> None:
        with open(self.path, "rb") as f:
            hdr = f.read(_HDR)
            if len(hdr) < _HDR or hdr[:4] != b"FRM8":
                raise ValueError(f"{self.path.name}: not a DSDIFF file (missing FRM8 header)")
            frm8_size = _u64(hdr, 4)

            form_type = f.read(4)
            if form_type != b"DSD ":
                raise ValueError(
                    f"{self.path.name}: unsupported form type {form_type!r} (expected 'DSD ')")

            end_pos = _HDR + frm8_size
            while f.tell() < end_pos:
                ch = f.read(_HDR)
                if len(ch) < _HDR:
                    break
                cid  = ch[:4]
                csz  = _u64(ch, 4)
                cpos = f.tell()

                if cid == b"FVER":
                    f.seek(csz, 1)
                elif cid == b"PROP":
                    self.prop_bytes = f.read(csz)
                    self._parse_prop(self.prop_bytes)
                elif cid == b"DSD ":
                    self.dsd_offset = cpos
                    self.dsd_size   = csz
                    f.seek(csz, 1)
                else:
                    f.seek(csz, 1)

                if csz % 2:
                    f.seek(1, 1)   # alignment pad

    def _parse_prop(self, prop: bytes) -> None:
        # prop starts with "SND " (4 bytes), followed by local chunks
        off = 4
        while off + _HDR <= len(prop):
            cid  = prop[off : off + 4]
            csz  = _u64(prop, off + 4)
            cdat = prop[off + _HDR : off + _HDR + csz]

            if cid == b"FS  ":
                self.sample_rate = _u32(cdat)
            elif cid == b"CHNL":
                self.channels = _u16(cdat)
            elif cid == b"CMPR":
                ctype = cdat[:4]
                if ctype != b"DSD ":
                    raise ValueError(
                        f"{self.path.name}: DST-compressed DFF files cannot be merged "
                        "with this tool. Use TASCAM Hi-Res Editor → Combine.")

            off += _HDR + csz
            if csz % 2:
                off += 1

    def stream_to(self, out_f, chunk_bytes: int = 1 << 20) -> None:
        """Write this file's DSD audio bytes to out_f without loading into RAM."""
        with open(self.path, "rb") as f:
            f.seek(self.dsd_offset)
            remaining = self.dsd_size
            while remaining > 0:
                buf = f.read(min(chunk_bytes, remaining))
                if not buf:
                    break
                out_f.write(buf)
                remaining -= len(buf)


# ── public API ────────────────────────────────────────────────────────────

def merge_dff(inputs: list[Path], output: Path,
              progress_cb=None) -> None:
    """Merge two or more uncompressed DFF files into a single output DFF.

    progress_cb(done_bytes, total_bytes) is called periodically if supplied.
    All inputs must share the same sample rate and channel layout.
    Raises ValueError on incompatible files or unsupported compression.
    """
    if len(inputs) < 2:
        raise ValueError("Need at least 2 input files to merge")

    readers = [_DffFile(p) for p in inputs]

    # Validate compatibility
    sr0, ch0 = readers[0].sample_rate, readers[0].channels
    for r in readers[1:]:
        if r.sample_rate != sr0:
            raise ValueError(
                f"Sample rate mismatch: {readers[0].path.name} ({sr0} Hz) "
                f"vs {r.path.name} ({r.sample_rate} Hz)")
        if r.channels != ch0:
            raise ValueError(
                f"Channel mismatch: {readers[0].path.name} ({ch0} ch) "
                f"vs {r.path.name} ({r.channels} ch)")

    total_dsd = sum(r.dsd_size for r in readers)

    # Build fixed sub-chunks
    fver_chunk = _chunk(b"FVER", struct.pack(">I", _DSDIFF_VERSION))
    prop_chunk = _chunk(b"PROP", readers[0].prop_bytes)
    dsd_pad    = 1 if total_dsd % 2 else 0

    # FRM8 payload = "DSD " + FVER + PROP + DSD header + DSD data [+ pad]
    form_payload_size = (4
                         + len(fver_chunk)
                         + len(prop_chunk)
                         + _HDR            # DSD chunk header (data written separately)
                         + total_dsd
                         + dsd_pad)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "wb") as out_f:
        out_f.write(_hdr(b"FRM8", form_payload_size))
        out_f.write(b"DSD ")               # form type
        out_f.write(fver_chunk)
        out_f.write(prop_chunk)
        out_f.write(_hdr(b"DSD ", total_dsd))  # DSD chunk header; data follows

        done = 0
        for r in readers:
            if progress_cb:
                # Wrap stream with progress reporting
                with open(r.path, "rb") as f:
                    f.seek(r.dsd_offset)
                    remaining = r.dsd_size
                    while remaining > 0:
                        buf = f.read(min(1 << 20, remaining))
                        if not buf:
                            break
                        out_f.write(buf)
                        done += len(buf)
                        remaining -= len(buf)
                        progress_cb(done, total_dsd)
            else:
                r.stream_to(out_f)

        if dsd_pad:
            out_f.write(b"\x00")
