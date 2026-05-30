import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

DEFAULTS: dict[str, Any] = {
    "flac_compression_level": 8,
    "flac_verify": True,
    "lame_vbr_quality": 2,
    "lame_cbr_bitrate": 320,
    "lame_abr_bitrate": 192,
    "mp3_mode": "VBR (variable bitrate)",
    "ape_compression_level": "normal",
    "tool_paths": {},
    "trackers": [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://tracker.openbittorrent.com:6969/announce",
        "udp://exodus.desync.com:6969/announce",
    ],
    "torrent_piece_size": "auto",
    "last_input_dir": "",
    "last_output_dir": "",
    "window_geometry": "820x520",
}


class Config:
    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path
        self.data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            disk = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        merged = dict(DEFAULTS)
        merged.update(disk)
        self.data = merged

    def save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)
