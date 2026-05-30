"""Bencode + .torrent creation. Pure Python, no external deps."""

import hashlib
import time
from pathlib import Path
from typing import Callable, Optional, Union


# ---------------------------------------------------------------
# Bencode
# ---------------------------------------------------------------

BEncodable = Union[int, bytes, str, list, dict]


def bencode(v: BEncodable) -> bytes:
    if isinstance(v, bool):
        # bool is a subclass of int in Python; treat as int
        v = int(v)
    if isinstance(v, int):
        return f"i{v}e".encode("ascii")
    if isinstance(v, bytes):
        return f"{len(v)}:".encode("ascii") + v
    if isinstance(v, str):
        return bencode(v.encode("utf-8"))
    if isinstance(v, list):
        return b"l" + b"".join(bencode(x) for x in v) + b"e"
    if isinstance(v, dict):
        out = b"d"
        # Keys must be byte-string-sorted per bencoding spec
        keys: list[bytes] = []
        kv: dict[bytes, BEncodable] = {}
        for k, val in v.items():
            kb = k.encode("utf-8") if isinstance(k, str) else k
            keys.append(kb)
            kv[kb] = val
        for k in sorted(keys):
            out += bencode(k) + bencode(kv[k])
        return out + b"e"
    raise TypeError(f"cannot bencode {type(v).__name__}")


def bdecode(data: bytes) -> BEncodable:
    pos = 0

    def parse() -> BEncodable:
        nonlocal pos
        c = data[pos:pos + 1]
        if c == b"i":
            end = data.index(b"e", pos)
            n = int(data[pos + 1:end])
            pos = end + 1
            return n
        if c == b"l":
            pos += 1
            out: list = []
            while data[pos:pos + 1] != b"e":
                out.append(parse())
            pos += 1
            return out
        if c == b"d":
            pos += 1
            out_d: dict = {}
            while data[pos:pos + 1] != b"e":
                key = parse()
                val = parse()
                out_d[key] = val
            pos += 1
            return out_d
        # string: <length>:<bytes>
        colon = data.index(b":", pos)
        n = int(data[pos:colon])
        pos = colon + 1 + n
        return data[colon + 1:pos]

    result = parse()
    if pos != len(data):
        raise ValueError("trailing data after bencoded value")
    return result


# ---------------------------------------------------------------
# Piece size selection
# ---------------------------------------------------------------

PIECE_SIZE_CHOICES = [
    ("Auto", 0),
    ("16 KiB", 1 << 14),
    ("32 KiB", 1 << 15),
    ("64 KiB", 1 << 16),
    ("128 KiB", 1 << 17),
    ("256 KiB", 1 << 18),
    ("512 KiB", 1 << 19),
    ("1 MiB", 1 << 20),
    ("2 MiB", 1 << 21),
    ("4 MiB", 1 << 22),
    ("8 MiB", 1 << 23),
    ("16 MiB", 1 << 24),
]


def auto_piece_size(total_bytes: int) -> int:
    """Aim for ~1000-2000 pieces; clamp to a sensible power-of-two range."""
    if total_bytes <= 0:
        return 1 << 18  # 256 KiB
    target = max(total_bytes // 1500, 1 << 14)
    # Round up to next power of two
    size = 1
    while size < target:
        size <<= 1
    return max(1 << 14, min(size, 1 << 24))  # clamp to [16 KiB, 16 MiB]


# ---------------------------------------------------------------
# Torrent creation
# ---------------------------------------------------------------

def create_torrent(
    source: Path,
    trackers: list[str],
    piece_size: int = 0,
    comment: str = "",
    private: bool = False,
    progress: Optional[Callable[[int, int], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> bytes:
    """Build a v1 .torrent file as bencoded bytes.

    `source` is a single file or a directory. `piece_size` of 0 picks automatically.
    `progress(bytes_done, bytes_total)` is called as pieces are hashed.
    `cancelled()` returning True aborts (raises RuntimeError)."""
    if not trackers:
        raise ValueError("at least one tracker URL required")

    if source.is_file():
        files = [source]
        total = source.stat().st_size
        is_dir = False
    elif source.is_dir():
        files = sorted(p for p in source.rglob("*") if p.is_file())
        total = sum(p.stat().st_size for p in files)
        is_dir = True
    else:
        raise FileNotFoundError(source)

    if piece_size <= 0:
        piece_size = auto_piece_size(total)

    pieces = bytearray()
    h = hashlib.sha1()
    written = 0
    bytes_done = 0
    chunk_size = 1 << 18  # 256 KiB read chunk

    for fp in files:
        with open(fp, "rb") as f:
            while True:
                if cancelled and cancelled():
                    raise RuntimeError("cancelled")
                need = piece_size - written
                buf = f.read(min(need, chunk_size))
                if not buf:
                    break
                h.update(buf)
                written += len(buf)
                bytes_done += len(buf)
                if written == piece_size:
                    pieces.extend(h.digest())
                    h = hashlib.sha1()
                    written = 0
                if progress:
                    progress(bytes_done, total)
    if written > 0:
        pieces.extend(h.digest())

    info: dict = {
        "name": source.name,
        "piece length": piece_size,
        "pieces": bytes(pieces),
    }
    if is_dir:
        info["files"] = [
            {"length": p.stat().st_size,
             "path": list(p.relative_to(source).parts)}
            for p in files
        ]
    else:
        info["length"] = total
    if private:
        info["private"] = 1

    torrent: dict = {
        "announce": trackers[0],
        "creation date": int(time.time()),
        "created by": "Trader's Little Jedi",
        "info": info,
    }
    if len(trackers) > 1:
        torrent["announce-list"] = [[t] for t in trackers]
    if comment:
        torrent["comment"] = comment

    return bencode(torrent)


# ---------------------------------------------------------------
# Inspection (for "Check torrent")
# ---------------------------------------------------------------

def inspect_torrent(path: Path) -> dict:
    """Parse a .torrent file and return a summary dict."""
    raw = path.read_bytes()
    d = bdecode(raw)
    if not isinstance(d, dict):
        raise ValueError("not a torrent file (top-level is not a dict)")

    def s(v):
        return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v

    info = d.get(b"info", {})
    if not isinstance(info, dict):
        raise ValueError("malformed torrent (no info dict)")

    name = s(info.get(b"name", b""))
    piece_length = info.get(b"piece length", 0)
    pieces_blob = info.get(b"pieces", b"")
    piece_count = len(pieces_blob) // 20 if isinstance(pieces_blob, bytes) else 0
    is_dir = b"files" in info
    if is_dir:
        files = []
        total = 0
        for fe in info.get(b"files", []):
            length = fe.get(b"length", 0)
            parts = [s(p) for p in fe.get(b"path", [])]
            files.append({"path": "/".join(parts), "length": length})
            total += length
    else:
        files = [{"path": name, "length": info.get(b"length", 0)}]
        total = info.get(b"length", 0)

    trackers: list[str] = []
    al = d.get(b"announce-list")
    if isinstance(al, list):
        for tier in al:
            if isinstance(tier, list):
                for t in tier:
                    trackers.append(s(t))
    ann = d.get(b"announce")
    if ann and s(ann) not in trackers:
        trackers.insert(0, s(ann))

    info_hash = hashlib.sha1(bencode(info)).hexdigest()

    return {
        "name": name,
        "info_hash": info_hash,
        "piece_length": piece_length,
        "piece_count": piece_count,
        "total_size": total,
        "is_dir": is_dir,
        "files": files,
        "trackers": trackers,
        "comment": s(d.get(b"comment", b"")),
        "created_by": s(d.get(b"created by", b"")),
        "creation_date": d.get(b"creation date", 0),
        "private": bool(info.get(b"private", 0)),
    }


def human_size(n: int) -> str:
    f = float(n)
    for u in ("B", "KiB", "MiB", "GiB", "TiB"):
        if f < 1024 or u == "TiB":
            return f"{f:.2f} {u}" if u != "B" else f"{int(f)} {u}"
        f /= 1024
    return f"{f:.2f} TiB"
