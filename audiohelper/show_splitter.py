"""Show Splitter — split a full-show recording into individual tracks.

Workflow:
  1. Load a WAV / FLAC / CAF / any-ffmpeg-readable show file.
  2. The waveform is decoded and drawn — zoomable, scrollable.
  3. Drag the orange marker handles to place track boundaries.
     Or: Load a setlist text file, or click Detect Silences.
  4. Click anywhere on the waveform to set the playback cursor,
     then press Play to preview ± 2 minutes from that point.
  5. Click Split & Tag — one FLAC/WAV/MP3 per track, fully tagged.
"""

import array
import os
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import theme as _t
from .action_picker import AUDIO_EXTS
from .auto_align import (
    SetlistEntry, align as align_boundaries, envelope_db, find_dips,
)
from .live_tagger import (
    EtreeShow, parse_etree_file, read_text_smart, generate_etree_file,
)
from .tc_sources import detect_source
from .tc_tagger import mutagen_available, write_tags as mutagen_write_tags
from .tools import get_tool


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(secs: float) -> str:
    if secs < 0:
        secs = 0.0
    total = int(secs)
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem, 60)
    frac   = secs - total
    fs     = f".{round(frac * 10):01d}" if frac >= 0.05 else ""
    return f"{h}:{m:02d}:{s:02d}{fs}" if h else f"{m}:{s:02d}{fs}"


def _parse_time(s: str) -> float:
    s = s.strip()
    parts = s.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(s)


def _nice_tick(visible_dur: float) -> float:
    for t in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600):
        if t >= visible_dur / 8:
            return float(t)
    return 3600.0


def _sanitize(name: str) -> str:
    import re
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "untitled"


# Default eTree filename template: "{abbrev}{date}d{set}t{track}", e.g.
# bruce2026-04-13d1t01.flac
DEFAULT_NAME_TEMPLATE = "%a%dd%Dt%n"


def _apply_name_template(template: str, abbrev: str, date: str,
                         disc: int, track: int, title: str, ext: str) -> str:
    """Substitute filename tokens. Tokens are case-sensitive:
       %a artist abbrev   %d date   %D set/disc #   %n track # (2-digit)
       %t track title
    """
    out = template
    out = out.replace("%a", abbrev)
    out = out.replace("%d", date)
    out = out.replace("%D", str(disc))
    out = out.replace("%n", f"{track:02d}")
    out = out.replace("%t", title)
    out = _sanitize(out)
    if not out.lower().endswith(ext):
        out += ext
    return out


SESSION_EXT = ".tljsplit"
_SESSION_HEADER = "# Trader's Little Jedi — Show Split Session (v1)"


def _session_filetypes():
    """File-dialog filters that are safe on macOS.

    macOS's native open/save panel (Tk 9 + Aqua) crashes when given a custom,
    unrecognized extension like '*.tljsplit'. So on macOS we only offer
    recognized patterns ('all files', '*.txt') and rely on the default
    extension when saving. Other platforms get the explicit filter.
    """
    import sys
    if sys.platform == "darwin":
        return [("All files", "*"), ("Text files", "*.txt")]
    return [("Show split session", f"*{SESSION_EXT}"),
            ("Text files", "*.txt"), ("All files", "*.*")]


def serialize_session(audio_path, meta: dict, tracks: list) -> str:
    """Render a human-readable, hand-editable session file.

    *meta* keys: source, artist, date, venue, location, abbrev, source_chain,
    format, template. *tracks* is a list of (start_sec, disc, title).
    """
    lines = [
        _SESSION_HEADER,
        "# Lines starting with # are comments. Edit times, sets, and titles",
        "# freely, then reload in the Show Splitter.",
        "",
        f"source       = {audio_path or ''}",
        f"artist       = {meta.get('artist', '')}",
        f"date         = {meta.get('date', '')}",
        f"venue        = {meta.get('venue', '')}",
        f"location     = {meta.get('location', '')}",
        f"abbrev       = {meta.get('abbrev', '')}",
        f"source_chain = {meta.get('source_chain', '')}",
        f"format       = {meta.get('format', 'FLAC')}",
        f"template     = {meta.get('template', DEFAULT_NAME_TEMPLATE)}",
        "",
        "# Tracks:  START | SET | TITLE",
        "[tracks]",
    ]
    for start, disc, title in tracks:
        lines.append(f"{_fmt_time(start):>10} | {disc} | {title}")
    return "\n".join(lines) + "\n"


def parse_session(text: str) -> dict:
    """Parse a session file into {'meta': {...}, 'tracks': [(start, disc, title)]}."""
    meta: dict = {}
    tracks: list = []
    in_tracks = False
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower() == "[tracks]":
            in_tracks = True
            continue
        if not in_tracks and "=" in line:
            key, _, val = line.partition("=")
            meta[key.strip().lower()] = val.strip()
            continue
        if in_tracks:
            # Split into at most 3 fields so a title may itself contain '|'.
            parts = s.split("|", 2)
            if len(parts) >= 3:
                try:
                    start = _parse_time(parts[0].strip())
                except ValueError:
                    continue
                try:
                    disc = int(parts[1].strip())
                except ValueError:
                    disc = 1
                tracks.append((start, disc, parts[2].strip()))
    return {"meta": meta, "tracks": tracks}


def _guess_abbrev(artist: str) -> str:
    """Guess an eTree-style abbreviation from an artist name.

    Uses the etree alias table when the artist is known, otherwise falls back
    to initials for multi-word names or the lowercased name for short ones.
    """
    import re
    a = artist.strip().lower()
    if not a:
        return ""
    known = {
        "grateful dead": "gd", "phish": "ph", "widespread panic": "wsp",
        "dave matthews band": "dmb", "allman brothers band": "abb",
        "string cheese incident": "sci", "umphrey's mcgee": "um",
        "dark star orchestra": "dso", "jerry garcia band": "jgb",
        "phil lesh & friends": "phil", "gov't mule": "mule",
        "leftover salmon": "ls", "yonder mountain string band": "ymsb",
        "bruce springsteen": "bruce", "grahame lesh & friends": "glaf",
        "railroad earth": "rre", "blues traveler": "bt", "moe.": "moe",
        "the disco biscuits": "db", "trey anastasio": "trey",
        "dogs in a pile": "dogs",
    }
    if a in known:
        return known[a]
    words = re.sub(r"[^\w\s&]", "", a).split()
    words = [w for w in words if w not in ("the", "and", "&", "of")]
    if len(words) == 1:
        return words[0][:8]
    return "".join(w[0] for w in words)[:6]


def parse_audacity_labels(content: str) -> list[tuple[float, float, str]]:
    """Parse an Audacity (or Logic-exported) label file into
    ``[(start_sec, end_sec, title), …]`` sorted by start time.

    The format is one label per line: ``start<TAB>end<TAB>title`` with times
    as plain seconds (``855.487600``). Audacity's optional spectral-selection
    rows (a second line starting with ``\\``) are skipped. Returns ``[]``
    unless *every* remaining non-blank line matches, so ordinary setlist
    text can never be mistaken for labels.
    """
    rows: list[tuple[float, float, str]] = []
    for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith("\\"):       # spectral-selection frequency row
            continue
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 2:
            return []
        try:
            start, end = float(parts[0]), float(parts[1])
        except ValueError:
            return []
        title = parts[2].strip() if len(parts) > 2 else ""
        rows.append((start, end, title))
    rows.sort(key=lambda r: r[0])
    return rows


def _probe(ffprobe: Path, path: Path) -> dict:
    import json
    try:
        r = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_format", "-show_streams",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    except Exception:
        return {}


def _duration(probe: dict) -> float:
    try:
        return float(probe.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        return 0.0


def _list_audio_devices(ffmpeg: Path) -> list[str]:
    """Return output device names available on this system."""
    import sys, re
    devices: list[str] = ["System default"]
    if sys.platform == "darwin":
        # CoreAudio via avfoundation
        try:
            r = subprocess.run(
                [str(ffmpeg), "-f", "avfoundation", "-list_devices", "true",
                 "-i", "dummy"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in r.stderr.splitlines():
                m = re.search(r'\[AVFoundation.*?\] \[(\d+)\] (.+)$', line)
                if m:
                    devices.append(f"{m.group(2)} [{m.group(1)}]")
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            r = subprocess.run(
                [str(ffmpeg), "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            for line in r.stderr.splitlines():
                if "DirectShow audio" in line or '"' in line:
                    m = re.search(r'"([^"]+)"', line)
                    if m:
                        devices.append(m.group(1))
        except Exception:
            pass
    return devices


def _detect_silences(ffmpeg: Path, path: Path,
                     noise_db: float = -40.0,
                     min_dur: float = 0.4) -> list[float]:
    import re
    cmd = [str(ffmpeg), "-i", str(path),
           "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
           "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return []
    ends = []
    for line in r.stderr.splitlines():
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m:
            ends.append(float(m.group(1)))
    return sorted(ends)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class SplitTrack:
    number: int
    title: str
    start_sec: float
    disc: int = 1
    disc_total: int = 1
    flagged: bool = False        # low-confidence proposed start (⚠ in the list)


# ── Waveform canvas ───────────────────────────────────────────────────────────

class WaveformView(tk.Frame):
    """Zoomable scrollable waveform with draggable split markers and playback."""

    RULER_H       = 22
    HANDLE_R      = 7
    WF_COLOR      = "#3d8fd1"
    WF_COLOR_DIM  = "#1e4a6e"
    MARKER_COLOR  = "#ff7043"
    CURSOR_COLOR  = "#ffffff"
    BG_COLOR      = "#121218"
    RULER_BG      = "#1e1e2e"
    RULER_FG      = "#aaaacc"

    def __init__(self, parent, on_marker_moved=None, on_seek=None, **kw):
        super().__init__(parent, bg=self.BG_COLOR, **kw)
        self._samples: list[float] = []
        self._decode_rate = 4000
        self._duration = 0.0
        self._zoom = 1.0          # pixels per second
        self._scroll_start = 0.0  # seconds at left edge
        self._markers: list[float] = []  # track boundary times (excludes 0.0)
        self._cursor = 0.0
        self._drag_idx: Optional[int] = None
        self._audio_path: Optional[Path] = None
        self._ffmpeg_path: Optional[Path] = None
        self._decoding = False
        self._play_proc: Optional[subprocess.Popen] = None
        self._play_after: Optional[str] = None
        self._play_wall = 0.0
        self._play_audio = 0.0
        self._preview_tmp: Optional[str] = None
        self.on_marker_moved = on_marker_moved
        self.on_seek = on_seek
        self._build()

    def _build(self) -> None:
        # Transport bar. Use ttk.Buttons so they pick up the app's dark theme —
        # raw tk.Buttons render as unreadable near-white widgets on macOS Aqua.
        tr = tk.Frame(self, bg=self.BG_COLOR)
        tr.pack(fill="x", side="bottom", padx=4, pady=(2, 2))
        self.btn_play = ttk.Button(tr, text="▶ Play", width=8,
                                   command=self._play, state="disabled")
        self.btn_play.pack(side="left", padx=(0, 2))
        self.btn_stop = ttk.Button(tr, text="■ Stop", width=7,
                                   style="Ghost.TButton",
                                   command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.lbl_time = tk.Label(tr, text="0:00:00", fg=_t.FG_PRIMARY,
                                 bg=self.BG_COLOR, font=("Consolas", 11),
                                 width=10, anchor="w")
        self.lbl_time.pack(side="left")
        tk.Label(tr, text="scroll = zoom · shift-scroll = pan · click = scrub",
                 fg=_t.FG_DIM, bg=self.BG_COLOR,
                 font=("Segoe UI", 9)).pack(side="left", padx=(10, 0))
        self._zoom_btns: list[ttk.Button] = []
        for label, cmd in (("Fit", self._zoom_fit),
                            ("−",   self._zoom_out),
                            ("+",   self._zoom_in)):
            b = ttk.Button(tr, text=label, command=cmd, state="disabled",
                           style="Ghost.TButton",
                           width=4 if label == "Fit" else 3)
            b.pack(side="right", padx=1)
            self._zoom_btns.append(b)

        # Audio output device selector
        tk.Label(tr, text="Out:", fg=_t.FG_SECONDARY, bg=self.BG_COLOR,
                 font=("Segoe UI", 9)).pack(side="right", padx=(16, 2))
        self._device_var = tk.StringVar(value="System default")
        self._device_menu = ttk.Combobox(
            tr, textvariable=self._device_var,
            values=["System default"], state="readonly", width=22)
        self._device_menu.pack(side="right", padx=(0, 4))
        # Populate devices after a short delay (ffmpeg subprocess)
        self.after(1500, self._populate_devices)

        # Scrollbar
        self.hbar = ttk.Scrollbar(self, orient="horizontal",
                                   command=self._on_hscroll)
        self.hbar.pack(fill="x", side="bottom")

        # Canvas
        self.canvas = tk.Canvas(self, bg=self.BG_COLOR,
                                 cursor="crosshair", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>",       lambda _e: self._redraw())
        self.canvas.bind("<ButtonPress-1>",   self._on_press)
        self.canvas.bind("<B1-Motion>",       self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        # Trackpad / wheel. Tk has no native pinch gesture, so the reliable
        # two-finger gesture (vertical scroll → MouseWheel) drives ZOOM, and
        # horizontal swipe (Shift-MouseWheel) or Shift+scroll PANS. Bound on
        # both the canvas and the frame so it works anywhere over the waveform.
        for w in (self.canvas, self):
            w.bind("<MouseWheel>",       self._on_scroll_zoom)
            w.bind("<Shift-MouseWheel>", self._on_scroll_pan)
            w.bind("<Option-MouseWheel>", self._on_scroll_pan)
            w.bind("<Button-4>",         self._on_scroll_zoom)
            w.bind("<Button-5>",         self._on_scroll_zoom)
        self._scrubbing = False

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, path: Path, ffmpeg: Path, duration: float,
             status_cb=None) -> None:
        self._audio_path = path
        self._ffmpeg_path = ffmpeg
        self._duration = duration
        self._samples = []
        self._markers = []
        self._cursor = 0.0
        self._scroll_start = 0.0
        self._decoding = True
        self._set_controls("disabled")
        self._redraw()
        if status_cb:
            status_cb("Decoding waveform...  (large files may take 30-60 s)")

        def _worker():
            samp, err = self._decode(path, ffmpeg)
            self.after(0, lambda: self._on_loaded(samp, err, status_cb))

        threading.Thread(target=_worker, daemon=True).start()

    def set_markers(self, times: list[float]) -> None:
        self._markers = sorted(t for t in times if t > 0.0)
        self._redraw()

    def set_cursor(self, t: float) -> None:
        self._cursor = max(0.0, min(t, self._duration))
        self.lbl_time.configure(text=_fmt_time(self._cursor))
        self._redraw()

    # ── Decode ────────────────────────────────────────────────────────────────

    def _decode(self, path: Path, ffmpeg: Path) -> tuple[list[float], str]:
        """Returns (samples, error_message). samples is [] on failure."""
        cmd = [str(ffmpeg), "-hide_banner", "-i", str(path),
               "-af", f"aresample={self._decode_rate}",
               "-ac", "1", "-f", "s16le", "-"]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            raw, err_bytes = proc.communicate()
            proc.wait()
        except Exception as e:
            return [], f"Could not launch ffmpeg: {e}"
        if not raw:
            err_text = err_bytes.decode("utf-8", errors="replace")[-300:] if err_bytes else ""
            return [], f"ffmpeg produced no output.\n{err_text}"
        buf = array.array("h")
        try:
            buf.frombytes(raw[:len(raw) - len(raw) % 2])
        except Exception as e:
            return [], f"Could not read PCM data: {e}"
        if not buf:
            return [], "Empty audio buffer after decode."
        step = max(1, len(buf) // 8_000_000)
        if step > 1:
            buf = array.array("h", (buf[i] for i in range(0, len(buf), step)))
        peak = max(abs(v) for v in buf) or 1
        return [abs(v) / peak for v in buf], ""

    def _on_loaded(self, samples: list[float], error: str, status_cb=None) -> None:
        self._decoding = False
        self._samples = samples
        if not samples:
            self._redraw()  # shows error overlay
            msg = f"Waveform decode failed: {error}"
            if status_cb:
                status_cb(msg)
            return
        self._set_controls("normal")
        self._zoom_fit()
        if status_cb:
            status_cb(f"Waveform ready  ({len(samples):,} samples)")

    def _set_controls(self, state: str) -> None:
        self.btn_play.configure(state=state)
        for b in self._zoom_btns:
            b.configure(state=state)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _canvas_w(self) -> int:
        return max(self.canvas.winfo_width(), 1)

    def _canvas_h(self) -> int:
        return max(self.canvas.winfo_height(), 1)

    def _sec_to_x(self, t: float) -> float:
        return (t - self._scroll_start) * self._zoom

    def _x_to_sec(self, x: float) -> float:
        return x / self._zoom + self._scroll_start

    def _show_message(self, msg: str) -> None:
        self.canvas.delete("all")
        cx, cy = self._canvas_w() // 2, self._canvas_h() // 2
        self.canvas.create_text(cx, cy, text=msg, fill="#777799",
                                font=("Segoe UI", 12))

    def _redraw(self) -> None:
        if not self._samples and self._duration == 0.0 and not self._decoding:
            return
        c = self.canvas
        c.delete("all")
        W = self._canvas_w()
        H = self._canvas_h()
        RH = self.RULER_H
        wf_h = H - RH

        # Background
        c.create_rectangle(0, 0, W, H, fill=self.BG_COLOR, outline="")
        c.create_rectangle(0, 0, W, RH, fill=self.RULER_BG, outline="")

        # Loading / error overlay (drawn on top at the end if needed)
        overlay_msg = ""
        if self._decoding:
            overlay_msg = "Decoding waveform...  (may take 30-60 s for large files)"
        elif self._duration > 0 and not self._samples:
            overlay_msg = "Waveform decode failed — check that ffmpeg is installed."

        # Time ruler
        if self._duration > 0:
            vis_dur = W / self._zoom
            tick = _nice_tick(vis_dur)
            t = (self._scroll_start // tick) * tick
            while t <= self._scroll_start + vis_dur + tick:
                x = self._sec_to_x(t)
                if 0 <= x <= W:
                    c.create_line(x, RH - 5, x, RH, fill=self.RULER_FG)
                    c.create_text(x, RH // 2, text=_fmt_time(t),
                                  fill=self.RULER_FG,
                                  font=("Consolas", 8), anchor="center")
                t += tick

        # Waveform
        if self._samples and self._duration > 0:
            n = len(self._samples)
            sps = n / self._duration  # samples per second
            mid = RH + wf_h // 2
            max_amp = wf_h // 2 - 2
            pts_top: list[float] = []
            pts_bot: list[float] = []
            for px in range(W):
                t0 = self._x_to_sec(px)
                t1 = self._x_to_sec(px + 1)
                i0 = max(0, int(t0 * sps))
                i1 = min(n,  int(t1 * sps) + 1)
                if i0 >= n:
                    break
                chunk = self._samples[i0:i1]
                pk = max(chunk) if chunk else 0.0
                amp = pk * max_amp
                pts_top += [px, mid - amp]
                pts_bot += [px, mid + amp]
            if len(pts_top) >= 4:
                # Reverse (x,y) PAIRS from pts_bot so the polygon closes
                # correctly right-to-left. reversed() alone would swap
                # individual floats (x↔y), producing the diagonal artifact.
                np = len(pts_bot) // 2
                pts_bot_r = [v for i in range(np - 1, -1, -1)
                             for v in (pts_bot[i * 2], pts_bot[i * 2 + 1])]
                poly = pts_top + pts_bot_r
                c.create_polygon(poly, fill=self.WF_COLOR, outline="",
                                 smooth=False)

        # Track markers
        for i, t in enumerate(self._markers):
            x = self._sec_to_x(t)
            if -20 < x < W + 20:
                c.create_line(x, RH, x, H, fill=self.MARKER_COLOR,
                              width=2, tags=f"m{i}")
                # Drag handle (downward triangle)
                r = self.HANDLE_R
                c.create_polygon(x - r, RH, x + r, RH, x, RH + r * 2,
                                 fill=self.MARKER_COLOR, outline="",
                                 tags=f"h{i}")
                # Track number badge
                c.create_text(x + 4, RH + 2, text=str(i + 2),
                              fill=self.MARKER_COLOR,
                              font=("Consolas", 8, "bold"), anchor="nw")

        # Playhead
        cx = self._sec_to_x(self._cursor)
        if 0 <= cx <= W:
            c.create_line(cx, 0, cx, H,
                          fill=self.CURSOR_COLOR, width=1, dash=(3, 4))

        # Overlay message (loading or error)
        if overlay_msg:
            c.create_rectangle(0, H // 2 - 18, W, H // 2 + 18,
                               fill="#1e1e2e", outline="")
            c.create_text(W // 2, H // 2, text=overlay_msg,
                          fill="#aaaacc", font=("Segoe UI", 11), anchor="center")

        # Scrollbar
        if self._duration > 0 and self._zoom > 0:
            total_px = self._duration * self._zoom
            lo = self._scroll_start / self._duration
            hi = min(1.0, lo + W / total_px)
            self.hbar.set(lo, hi)

    # ── Zoom / scroll ─────────────────────────────────────────────────────────

    def _zoom_fit(self) -> None:
        if self._duration > 0:
            self._zoom = self._canvas_w() / self._duration
            self._scroll_start = 0.0
            self._redraw()

    def _zoom_in(self) -> None:
        self._zoom_around(2.0)

    def _zoom_out(self) -> None:
        self._zoom_around(0.5)

    def _zoom_around(self, factor: float,
                     pivot_sec: Optional[float] = None) -> None:
        if pivot_sec is None:
            pivot_sec = self._x_to_sec(self._canvas_w() / 2)
        x_before = self._sec_to_x(pivot_sec)
        self._zoom = max(self._canvas_w() / max(self._duration, 1), self._zoom * factor)
        self._scroll_start = pivot_sec - x_before / self._zoom
        self._clamp_scroll()
        self._redraw()

    def _clamp_scroll(self) -> None:
        visible = self._canvas_w() / self._zoom
        self._scroll_start = max(0.0,
                                 min(self._scroll_start, self._duration - visible))

    def _on_hscroll(self, *args) -> None:
        if args[0] == "moveto":
            self._scroll_start = float(args[1]) * self._duration
        elif args[0] == "scroll":
            self._scroll_start += int(args[1]) * self._duration * 0.1
        self._clamp_scroll()
        self._redraw()

    def _wheel_delta(self, event) -> float:
        """Normalized wheel/scroll delta: + = up/right, - = down/left."""
        if getattr(event, "num", None) == 4:
            return 1.0
        if getattr(event, "num", None) == 5:
            return -1.0
        d = getattr(event, "delta", 0)
        # macOS deltas are small (±1..±3); Windows are ±120.
        return d / 120.0 if abs(d) >= 30 else float(d)

    def _on_scroll_pan(self, event) -> str:
        """Two-finger scroll → traverse (pan) the waveform horizontally."""
        if self._duration <= 0:
            return "break"
        delta = self._wheel_delta(event)
        visible = self._canvas_w() / self._zoom
        self._scroll_start -= delta * visible * 0.15
        self._clamp_scroll()
        self._redraw()
        return "break"

    def _on_scroll_zoom(self, event) -> str:
        """Two-finger scroll → zoom around the pointer. Factor scales with the
        scroll delta so trackpad (many small deltas) zooms smoothly and a mouse
        wheel (one big notch) zooms in clear steps."""
        delta = self._wheel_delta(event)
        if delta == 0:
            return "break"
        factor = 1.0 + 0.18 * max(-3.0, min(3.0, delta))
        factor = max(0.5, min(2.0, factor))
        self._zoom_around(factor, self._x_to_sec(event.x))
        return "break"

    # ── Mouse interaction ─────────────────────────────────────────────────────

    def _nearest_marker(self, x: float) -> Optional[int]:
        for i, t in enumerate(self._markers):
            if abs(self._sec_to_x(t) - x) <= self.HANDLE_R * 2:
                return i
        return None

    def _on_press(self, event) -> None:
        idx = self._nearest_marker(event.x)
        if idx is not None:
            # Grab a marker to drag the track boundary.
            self._drag_idx = idx
            self.canvas.config(cursor="sb_h_double_arrow")
        else:
            # Click empty waveform → scrub the playhead (and keep scrubbing on
            # drag). Playback follows the cursor when you hit Play / space.
            self._scrubbing = True
            self._set_cursor_from_x(event.x)

    def _set_cursor_from_x(self, x: float) -> None:
        self._cursor = max(0.0, min(self._x_to_sec(x), self._duration))
        self.lbl_time.configure(text=_fmt_time(self._cursor))
        self._redraw()
        if self.on_seek:
            self.on_seek(self._cursor)

    def _on_drag(self, event) -> None:
        if self._drag_idx is not None:
            new_t = max(0.1, min(self._x_to_sec(event.x), self._duration - 0.1))
            i = self._drag_idx
            if i > 0:
                new_t = max(new_t, self._markers[i - 1] + 1.0)
            if i < len(self._markers) - 1:
                new_t = min(new_t, self._markers[i + 1] - 1.0)
            self._markers[i] = new_t
            self.lbl_time.configure(text=_fmt_time(new_t))
            self._redraw()
        elif self._scrubbing:
            self._set_cursor_from_x(event.x)

    def _on_release(self, event) -> None:
        if self._drag_idx is not None:
            if self.on_marker_moved:
                self.on_marker_moved(self._drag_idx, self._markers[self._drag_idx])
            self._drag_idx = None
            self.canvas.config(cursor="crosshair")
        self._scrubbing = False

    # ── Playback ──────────────────────────────────────────────────────────────

    def play_from(self, sec: float) -> None:
        """Move the playhead to *sec*, scroll it into view, and start playing."""
        self.set_cursor(sec)
        # Scroll so the cursor sits ~10% from the left edge.
        if self._duration > 0:
            self._scroll_start = max(0.0, sec - self._canvas_w() / self._zoom * 0.1)
            self._clamp_scroll()
            self._redraw()
        self._play()

    def toggle_play(self) -> None:
        """Spacebar handler: stop if playing, else play from the cursor."""
        if self._play_proc and self._play_proc.poll() is None:
            self._stop()
        elif self._samples:
            self._play()

    def detect_quiet_boundaries(self, target: int = 0,
                                min_gap: float = 25.0,
                                min_quiet: float = 0.6) -> list[float]:
        """Find track boundaries from the decoded envelope — the points where
        audio RESUMES after a quiet dip. Far more reliable for live recordings
        than a fixed-dB silencedetect, because the threshold adapts to the
        recording's own level (applause/crowd gaps are quiet *relative* to the
        music, not absolutely silent).

        Returns resume timestamps (each = start of the next track), filtered so
        no two are closer than *min_gap* seconds.
        """
        n = len(self._samples)
        if n == 0 or self._duration <= 0:
            return []
        sps = n / self._duration                    # envelope samples / sec
        win = max(1, int(0.4 * sps))                # ~0.4 s smoothing
        env = self._samples

        # Smoothed envelope via a running mean (cheap prefix-sum).
        prefix = [0.0] * (n + 1)
        for i in range(n):
            prefix[i + 1] = prefix[i] + env[i]
        def smooth(i: int) -> float:
            a = max(0, i - win); b = min(n, i + win)
            return (prefix[b] - prefix[a]) / (b - a)

        # Adaptive threshold: a fraction of the median level. Sample every few
        # points for speed on long shows.
        step = max(1, n // 20000)
        sampled = sorted(smooth(i) for i in range(0, n, step))
        if not sampled:
            return []
        median = sampled[len(sampled) // 2]
        thresh = max(median * 0.18, 0.012)          # quiet = well below median
        min_quiet_samples = int(min_quiet * sps)

        boundaries: list[float] = []
        i = 0
        in_quiet = False
        quiet_start = 0
        while i < n:
            s = smooth(i)
            if s < thresh:
                if not in_quiet:
                    in_quiet = True
                    quiet_start = i
            else:
                if in_quiet:
                    in_quiet = False
                    if i - quiet_start >= min_quiet_samples:
                        # Resume point = where audio comes back.
                        boundaries.append(round(i / sps, 2))
                i += step          # coarse scan while loud
                continue
            i += 1

        # Enforce a minimum gap between boundaries.
        filtered: list[float] = []
        for t in boundaries:
            if not filtered or t - filtered[-1] >= min_gap:
                filtered.append(t)
        return filtered

    def _play(self) -> None:
        self._stop()
        if not self._audio_path or not self._ffmpeg_path:
            return
        start = self._cursor
        self._play_wall  = time.monotonic()
        self._play_audio = start
        self.btn_play.configure(state="disabled")
        self.btn_stop.configure(state="normal")

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        self._preview_tmp = tmp.name

        ffmpeg = self._ffmpeg_path

        def _extract():
            cmd = [str(ffmpeg), "-y", "-hide_banner",
                   "-ss", str(start), "-t", "120",
                   "-i", str(self._audio_path),
                   "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2",
                   tmp.name]
            try:
                subprocess.run(cmd, capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               timeout=60)
                self.after(0, self._launch_player)
            except Exception:
                self.after(0, lambda: self.btn_play.configure(state="normal"))

        threading.Thread(target=_extract, daemon=True).start()

    def _populate_devices(self) -> None:
        if not self._ffmpeg_path or not self._ffmpeg_path.exists():
            return
        def _worker():
            devs = _list_audio_devices(self._ffmpeg_path)
            self.after(0, lambda: self._device_menu.configure(values=devs))
        threading.Thread(target=_worker, daemon=True).start()

    def _launch_player(self) -> None:
        import sys
        tmp = self._preview_tmp
        if not tmp or not Path(tmp).exists():
            self.btn_play.configure(state="normal")
            return
        device = self._device_var.get() if hasattr(self, "_device_var") else "System default"
        if sys.platform == "darwin":
            # afplay on default device; ffplay for specific device
            if device and device != "System default" and self._ffmpeg_path:
                # Extract device index from "Name [N]" format
                import re as _re
                m = _re.search(r'\[(\d+)\]$', device)
                if m:
                    ffplay = self._ffmpeg_path.parent / "ffplay"
                    if not ffplay.exists():
                        ffplay = Path("ffplay")
                    cmd = [str(ffplay), "-nodisp", "-autoexit",
                           "-audio_device_index", m.group(1), tmp]
                else:
                    cmd = ["afplay", tmp]
            else:
                cmd = ["afplay", tmp]
        elif sys.platform == "win32":
            ffplay = (self._ffmpeg_path.parent / "ffplay.exe" if self._ffmpeg_path else None)
            if ffplay and ffplay.exists():
                cmd = [str(ffplay), "-nodisp", "-autoexit", tmp]
            else:
                cmd = ["start", "/b", tmp]
        else:
            cmd = ["aplay", tmp]
        try:
            self._play_proc = subprocess.Popen(
                cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._tick_playhead()
        except Exception:
            self.btn_play.configure(state="normal")

    def _tick_playhead(self) -> None:
        if self._play_proc and self._play_proc.poll() is None:
            elapsed = time.monotonic() - self._play_wall
            t = self._play_audio + elapsed
            self.set_cursor(t)
            # Auto-scroll
            W = self._canvas_w()
            px = self._sec_to_x(t)
            if px < 0 or px > W - 30:
                self._scroll_start = max(0.0, t - W / self._zoom * 0.1)
                self._clamp_scroll()
            self._play_after = self.after(80, self._tick_playhead)
        else:
            self._stop_no_proc()

    def _stop(self) -> None:
        if self._play_proc:
            try:
                self._play_proc.terminate()
            except Exception:
                pass
            self._play_proc = None
        self._stop_no_proc()

    def _stop_no_proc(self) -> None:
        if self._play_after:
            self.after_cancel(self._play_after)
            self._play_after = None
        self.btn_stop.configure(state="disabled")
        self.btn_play.configure(state="normal" if self._samples else "disabled")
        tmp = getattr(self, "_preview_tmp", None)
        if tmp:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass
            self._preview_tmp = None


# ── Main dialog ───────────────────────────────────────────────────────────────

class ShowSplitterDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Show Splitter")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("1200x780")
        self.minsize(900, 580)

        self._audio_path: Optional[Path] = None
        self._duration: float = 0.0
        self._tracks: list[SplitTrack] = []
        self._show: Optional[EtreeShow] = None
        self._loading_row = False

        self._build_toolbar()
        self._build_body()
        self._build_bottom_bar()
        self.status = ttk.Label(self, text="Load a show file to begin.",
                                anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        # Spacebar toggles play/stop (unless typing in an entry field).
        self.bind_all("<space>", self._on_space)

        if initial_files:
            audio = [f for f in initial_files
                     if Path(f).suffix.lower() in AUDIO_EXTS]
            if audio:
                self.after(80, lambda: self._load_audio(Path(audio[0])))

    def _on_space(self, event) -> Optional[str]:
        # Don't hijack space while the user is typing in a text field.
        w = self.focus_get()
        if isinstance(w, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox)):
            return None
        self.waveform.toggle_play()
        return "break"

    def destroy(self) -> None:
        # Stop any preview playback so audio doesn't keep playing after close.
        try:
            self.waveform._stop()
        except Exception:
            pass
        try:
            self.unbind_all("<space>")
        except Exception:
            pass
        super().destroy()

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Load show file…", command=self._browse_audio).pack(side="left")
        ttk.Button(bar, text="Load setlist…",   command=self._load_txt_dialog).pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self.btn_silence = ttk.Button(bar, text="Detect silences",
                                      command=self._run_silence_detect, state="disabled")
        self.btn_silence.pack(side="left")
        self.btn_propose = ttk.Button(bar, text="Propose splits",
                                      command=self._run_propose, state="disabled")
        self.btn_propose.pack(side="left", padx=4)
        ttk.Button(bar, text="Even split", command=self._even_split).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="Add track",  command=self._add_track).pack(side="left")
        ttk.Button(bar, text="Remove",     command=self._remove_track).pack(side="left", padx=4)
        ttk.Button(bar, text="↑",  width=3, command=self._move_up).pack(side="left")
        ttk.Button(bar, text="↓",  width=3, command=self._move_down).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="Save session…", command=self._save_session).pack(side="left")
        ttk.Button(bar, text="Load session…", command=self._load_session).pack(side="left", padx=4)

    # ── Body ──────────────────────────────────────────────────────────────────

    def _build_body(self) -> None:
        body = ttk.Frame(self, padding=(8, 4, 8, 0))
        body.pack(fill="both", expand=True)

        # Info strip
        info_frame = ttk.Frame(body)
        info_frame.pack(fill="x", pady=(0, 4))
        self.lbl_file = ttk.Label(info_frame, text="(no file loaded)",
                                  foreground="gray")
        self.lbl_file.pack(side="left")
        self.lbl_info = ttk.Label(info_frame, text="", foreground="#888888")
        self.lbl_info.pack(side="left", padx=(12, 0))

        # Waveform — takes most vertical space
        self.waveform = WaveformView(
            body,
            on_marker_moved=self._on_marker_moved,
            on_seek=self._on_waveform_seek,
            height=260,
        )
        self.waveform.pack(fill="both", expand=True, pady=(0, 4))

        # Lower paned: show data (left) + track list (right)
        paned = ttk.PanedWindow(body, orient="horizontal")
        paned.pack(fill="both", expand=False, pady=(0, 0))

        # Show data
        left = ttk.LabelFrame(paned, text="Show data", padding=8)
        left.columnconfigure(1, weight=1)
        self._field_vars: dict[str, tk.StringVar] = {}
        for row_i, (label, key) in enumerate([
            ("Artist",   "ARTIST"),
            ("Date",     "DATE"),
            ("Venue",    "VENUE"),
            ("Location", "LOCATION"),
            ("Abbrev",   "ABBREV"),
        ]):
            ttk.Label(left, text=f"{label}:").grid(row=row_i, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self._field_vars[key] = var
            ttk.Entry(left, textvariable=var).grid(row=row_i, column=1,
                                                    sticky="ew", padx=(6, 0), pady=2)
        # Keep the eTree filename prefix in sync as artist/date/abbrev change.
        self._field_vars["ABBREV"].trace_add("write", lambda *_: self._refresh_name_preview())
        self._field_vars["DATE"].trace_add("write", lambda *_: self._refresh_name_preview())
        ttk.Label(left, text="Source:").grid(row=5, column=0, sticky="nw", pady=2)
        self._source_widget = tk.Text(left, height=3, font=("Segoe UI", 9), wrap="word")
        self._source_widget.grid(row=5, column=1, sticky="ew", padx=(6, 0), pady=2)
        paned.add(left, weight=1)

        # Track list
        right = ttk.Frame(paned)
        tl = ttk.LabelFrame(right, text="Track list", padding=4)
        tl.pack(fill="both", expand=True)
        cols = ("num", "start", "title", "disc")
        self.tv = ttk.Treeview(tl, columns=cols, show="headings",
                               selectmode="browse", height=7)
        self.tv.heading("num",   text="#",     anchor="center")
        self.tv.heading("start", text="Start", anchor="w")
        self.tv.heading("title", text="Title", anchor="w")
        self.tv.heading("disc",  text="Set",   anchor="center")
        self.tv.column("num",   width=34,  minwidth=28,  stretch=False, anchor="center")
        self.tv.column("start", width=80,  minwidth=60,  stretch=False, anchor="w")
        self.tv.column("title", width=260, minwidth=100, stretch=True,  anchor="w")
        self.tv.column("disc",  width=38,  minwidth=30,  stretch=False, anchor="center")
        tv_sb = ttk.Scrollbar(tl, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=tv_sb.set)
        tv_sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<<TreeviewSelect>>", self._on_row_select)
        self.tv.bind("<ButtonRelease-1>", self._on_row_click)  # click → audition

        # Inline edit strip
        edit = ttk.Frame(right)
        edit.pack(fill="x", pady=(2, 0))
        ttk.Label(edit, text="Start:").grid(row=0, column=0, sticky="w")
        self.var_start = tk.StringVar()
        ttk.Entry(edit, textvariable=self.var_start, width=10).grid(
            row=0, column=1, sticky="w", padx=(4, 0))
        ttk.Label(edit, text="Title:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.var_title_edit = tk.StringVar()
        ttk.Entry(edit, textvariable=self.var_title_edit).grid(
            row=0, column=3, sticky="ew", padx=(4, 0))
        ttk.Label(edit, text="Set:").grid(row=0, column=4, sticky="w", padx=(8, 0))
        self.var_disc_edit = tk.StringVar()
        ttk.Entry(edit, textvariable=self.var_disc_edit, width=4).grid(
            row=0, column=5, sticky="w", padx=(4, 0))
        ttk.Button(edit, text="Apply", command=self._apply_edit).grid(
            row=0, column=6, padx=(8, 0))
        edit.columnconfigure(3, weight=1)
        paned.add(right, weight=2)

    # ── Bottom bar ────────────────────────────────────────────────────────────

    def _build_bottom_bar(self) -> None:
        # ── Filename template row ─────────────────────────────────────────────
        name_bar = ttk.Frame(self, padding=(8, 2))
        name_bar.pack(fill="x", side="bottom")
        ttk.Label(name_bar, text="Filename:").pack(side="left")
        self.var_nametpl = tk.StringVar(
            value=self.config_obj.get("splitter_name_template", DEFAULT_NAME_TEMPLATE))
        ent = ttk.Entry(name_bar, textvariable=self.var_nametpl, width=26)
        ent.pack(side="left", padx=(4, 4))
        self.var_nametpl.trace_add("write", lambda *_: self._refresh_name_preview())
        ttk.Button(name_bar, text="?", width=2, style="Ghost.TButton",
                   command=self._show_name_help).pack(side="left")
        self.lbl_name_preview = ttk.Label(name_bar, text="", style="Dim.TLabel")
        self.lbl_name_preview.pack(side="right")
        ttk.Label(name_bar, text="e.g.", style="Dim.TLabel").pack(side="right", padx=(0, 4))

        # ── Package row: what to put in the finished folder ───────────────────
        pkg = ttk.LabelFrame(self, text="Also create in the output folder", padding=(8, 4))
        pkg.pack(fill="x", side="bottom")
        self.var_pkg_txt     = tk.BooleanVar(value=True)
        self.var_pkg_ffp     = tk.BooleanVar(value=True)
        self.var_pkg_md5     = tk.BooleanVar(value=False)
        self.var_pkg_embed   = tk.BooleanVar(value=True)
        self.var_pkg_torrent = tk.BooleanVar(value=False)
        ttk.Checkbutton(pkg, text="Setlist .txt", variable=self.var_pkg_txt).pack(side="left")
        ttk.Checkbutton(pkg, text="FFP checksum", variable=self.var_pkg_ffp).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(pkg, text="MD5", variable=self.var_pkg_md5).pack(side="left", padx=(10, 0))
        ttk.Separator(pkg, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(pkg, text="Cover:").pack(side="left")
        self.var_cover = tk.StringVar()
        ttk.Entry(pkg, textvariable=self.var_cover, width=18).pack(side="left", padx=(4, 2))
        ttk.Button(pkg, text="…", width=2, command=self._browse_cover).pack(side="left")
        ttk.Checkbutton(pkg, text="embed", variable=self.var_pkg_embed).pack(side="left", padx=(4, 0))
        ttk.Separator(pkg, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Checkbutton(pkg, text="Torrent", variable=self.var_pkg_torrent).pack(side="left")
        self.var_tracker = tk.StringVar(value=self.config_obj.get("torrent_default_tracker_url", ""))
        ttk.Entry(pkg, textvariable=self.var_tracker, width=18).pack(side="left", padx=(4, 0))
        ttk.Label(pkg, text="tracker URL", style="Dim.TLabel").pack(side="left", padx=(4, 0))
        ttk.Separator(pkg, orient="vertical").pack(side="left", fill="y", padx=8)
        self.var_pkg_lma = tk.BooleanVar(value=False)
        ttk.Checkbutton(pkg, text="Upload to Archive.org (LMA) after",
                        variable=self.var_pkg_lma).pack(side="left")

        # ── Output folder / format / actions row ──────────────────────────────
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x", side="bottom")
        ttk.Label(bar, text="Output folder:").pack(side="left")
        self.var_outdir = tk.StringVar()
        ttk.Entry(bar, textvariable=self.var_outdir, width=40).pack(
            side="left", padx=(4, 0), fill="x", expand=True)
        ttk.Button(bar, text="Browse…", command=self._browse_outdir).pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.var_fmt = tk.StringVar(value="FLAC")
        for fmt in ("FLAC", "WAV", "MP3"):
            ttk.Radiobutton(bar, text=fmt, variable=self.var_fmt, value=fmt).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.btn_split = ttk.Button(bar, text="Split & Package",
                                    style="Action.TButton",
                                    command=self._split_and_tag, state="disabled")
        self.btn_split.pack(side="right", padx=4)
        self.progress = ttk.Progressbar(bar, mode="determinate", length=180)
        self.progress.pack(side="right", padx=(0, 8))
        self._refresh_name_preview()

    def _refresh_name_preview(self) -> None:
        """Show a live example of the filename template for track 1, disc 1."""
        if not hasattr(self, "lbl_name_preview"):
            return
        abbrev = self._field_vars.get("ABBREV").get().strip() if hasattr(self, "_field_vars") else ""
        date   = self._field_vars.get("DATE").get().strip() if hasattr(self, "_field_vars") else ""
        ext = {"FLAC": ".flac", "WAV": ".wav", "MP3": ".mp3"}.get(
            getattr(self, "var_fmt", tk.StringVar(value="FLAC")).get(), ".flac")
        example = _apply_name_template(
            self.var_nametpl.get(), abbrev or "band", date or "2026-01-01",
            disc=1, track=1, title="Opening", ext=ext)
        self.lbl_name_preview.configure(text=example)

    def _show_name_help(self) -> None:
        """Popup explaining the filename format tokens (XLD-style)."""
        dlg = tk.Toplevel(self)
        dlg.title("Filename format")
        dlg.transient(self)
        dlg.resizable(False, False)
        _t.apply(dlg)
        dlg.configure(bg=_t.BG_DEEP)

        frm = ttk.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Filename format tokens",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(frm, style="Dim.TLabel",
                  text="Build output filenames from these tokens. Anything else "
                       "(letters, digits, dashes) is kept literally.").pack(
            anchor="w", pady=(2, 10))

        grid = ttk.Frame(frm)
        grid.pack(fill="x")
        rows = [
            ("%a", "Artist abbreviation", "bruce"),
            ("%d", "Date (YYYY-MM-DD)", "2026-04-13"),
            ("%D", "Set / disc number", "1"),
            ("%n", "Track number (2-digit)", "01"),
            ("%t", "Track title", "Rosalita"),
        ]
        for r, (tok, desc, ex) in enumerate(rows):
            ttk.Label(grid, text=tok, font=("Consolas", 11, "bold"),
                      foreground=_t.ACCENT_INFO).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=2)
            ttk.Label(grid, text=desc).grid(row=r, column=1, sticky="w", padx=(0, 16))
            ttk.Label(grid, text=f"→ {ex}", style="Dim.TLabel").grid(
                row=r, column=2, sticky="w")

        ttk.Separator(frm).pack(fill="x", pady=10)
        ttk.Label(frm, text="Examples", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        for tpl, out in [
            ("%a%dd%Dt%n", "bruce2026-04-13d1t01.flac   (eTree standard)"),
            ("%a%dd%Dt%n %t", "bruce2026-04-13d1t01 Rosalita.flac"),
            ("%D-%n %t", "1-01 Rosalita.flac"),
        ]:
            row = ttk.Frame(frm)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=tpl, font=("Consolas", 10),
                      foreground=_t.ACCENT_PRIMARY, width=16).pack(side="left")
            ttk.Label(row, text=out, style="Dim.TLabel").pack(side="left")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Use eTree standard (%a%dd%Dt%n)",
                   command=lambda: (self.var_nametpl.set(DEFAULT_NAME_TEMPLATE),
                                    dlg.destroy())).pack(side="left")
        ttk.Button(btns, text="Close", command=dlg.destroy).pack(side="right")

    # ── Audio loading ─────────────────────────────────────────────────────────

    def _browse_audio(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        f = filedialog.askopenfilename(
            parent=self, title="Select show file",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("Audio files", exts), ("All files", "*.*")],
        )
        if f:
            self._load_audio(Path(f))

    def _load_audio(self, path: Path) -> None:
        ffprobe = get_tool("ffprobe").path(self.config_obj)
        ffmpeg  = get_tool("ffmpeg").path(self.config_obj)
        if not ffprobe.exists():
            messagebox.showerror("Show Splitter",
                                 "ffprobe not found. Install via Tools → Update all CLI tools.")
            return
        self._audio_path = path
        self.config_obj["last_input_dir"] = str(path.parent)
        self.config_obj.save()
        self.lbl_file.configure(text=path.name, foreground="")

        def _probe_worker():
            probe = _probe(ffprobe, path)
            dur   = _duration(probe)
            self.after(0, lambda: self._on_probe_done(probe, dur, ffmpeg, path))

        threading.Thread(target=_probe_worker, daemon=True).start()

    def _on_probe_done(self, probe: dict, dur: float,
                       ffmpeg: Path, path: Path) -> None:
        self._duration = dur
        streams = probe.get("streams", [{}])
        audio_s = next((s for s in streams if s.get("codec_type") == "audio"), {})
        parts = []
        if dur:
            h, rem = divmod(int(dur), 3600)
            m, s   = divmod(rem, 60)
            parts.append(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
        if audio_s.get("sample_rate"):
            parts.append(f"{int(audio_s['sample_rate']):,} Hz")
        bits = audio_s.get("bits_per_sample") or audio_s.get("bits_per_raw_sample")
        if bits:
            parts.append(f"{bits}-bit")
        ch = audio_s.get("channels")
        if ch:
            parts.append("stereo" if ch == 2 else f"{ch}ch")
        self.lbl_info.configure(text="  •  ".join(parts))

        if not self.var_outdir.get():
            self.var_outdir.set(str(path.parent))

        self.btn_silence.configure(state="normal" if dur > 0 else "disabled")
        self.btn_propose.configure(state="normal" if dur > 0 else "disabled")
        self.waveform.load(path, ffmpeg, dur,
                           status_cb=lambda msg: self.status.configure(text=msg))
        # Even-split only when tracks came from a setlist with no real times.
        # A restored session already has start times — preserve them.
        if self._tracks and not getattr(self, "_preserve_track_times", False):
            self._even_split()
        else:
            self._sync_markers()
        self._preserve_track_times = False
        self._update_split_btn()

    # ── Setlist loading ───────────────────────────────────────────────────────

    def _load_txt_dialog(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Select setlist or Audacity label file",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if f:
            self._load_txt_path(Path(f))

    def _load_txt_path(self, p: Path) -> None:
        try:
            content = read_text_smart(p)
        except OSError as e:
            messagebox.showerror("Show Splitter", f"Cannot read {p.name}:\n{e}")
            return
        labels = parse_audacity_labels(content)
        if labels:
            self._tracks_from_labels(labels)
            self.status.configure(
                text=f"Audacity labels: {p.name}  ({len(self._tracks)} tracks)")
            return
        show = parse_etree_file(content)
        self._show = show
        self._field_vars["ARTIST"].set(show.artist)
        self._field_vars["DATE"].set(show.date)
        self._field_vars["VENUE"].set(show.venue)
        self._field_vars["LOCATION"].set(show.location)
        if not self._field_vars["ABBREV"].get().strip():
            self._field_vars["ABBREV"].set(_guess_abbrev(show.artist))
        self._source_widget.delete("1.0", "end")
        self._source_widget.insert("1.0", show.source)
        self._tracks_from_show(show)
        self.status.configure(text=f"Setlist: {p.name}  ({len(self._tracks)} tracks)")

    def _tracks_from_show(self, show: EtreeShow) -> None:
        n_sets = len(show.sets)
        new: list[SplitTrack] = []
        for si, s in enumerate(show.sets, 1):
            for t in s.tracks:
                new.append(SplitTrack(number=t.global_index, title=t.title,
                                      start_sec=0.0, disc=si, disc_total=n_sets))
        self._tracks = new
        self._even_split()
        self._refresh_tv()
        self._update_split_btn()

    def _tracks_from_labels(self, labels: list[tuple[float, float, str]]) -> None:
        """Replace the track list with exact start times from a label file.
        Keeps titles already loaded from a setlist when the labels carry none."""
        old_titles = [t.title for t in self._tracks]
        new: list[SplitTrack] = []
        for i, (start, _end, title) in enumerate(labels, 1):
            if not title and i <= len(old_titles):
                title = old_titles[i - 1]
            new.append(SplitTrack(number=i, title=title or f"Track {i:02d}",
                                  start_sec=round(start, 2)))
        self._tracks = new
        self._sync_markers()
        self._refresh_tv()
        self._update_split_btn()

    # ── Split-point helpers ───────────────────────────────────────────────────

    def _even_split(self) -> None:
        n = len(self._tracks)
        if n == 0 or self._duration <= 0:
            return
        step = self._duration / n
        for i, t in enumerate(self._tracks):
            t.start_sec = round(i * step, 2)
        self._sync_markers()
        self._refresh_tv()

    def _run_propose(self) -> None:
        """Propose a full set of split points from the setlist + audio.

        Uses the decoded envelope (dips) and, when faster-whisper is
        installed, a local transcription pass so song titles sung in
        choruses anchor the boundaries. Low-confidence proposals are
        flagged ⚠ in the track list for auditioning.
        """
        if self._duration <= 0 or not self.waveform._samples:
            self.status.configure(text="Wait for the waveform to finish decoding.")
            return
        if len(self._tracks) < 2:
            self.status.configure(
                text="Load a setlist first — proposals need the track count and titles.")
            return
        setlist = [SetlistEntry(t.title.rstrip(" >").strip(),
                                t.title.rstrip().endswith(">"))
                   for t in self._tracks]
        samples = self.waveform._samples
        duration, path = self._duration, self._audio_path
        self.btn_propose.configure(state="disabled")
        self.status.configure(text="Analyzing envelope…")

        def _status(msg: str) -> None:
            self.after(0, lambda: self.status.configure(text=msg))

        def _worker():
            env, frame = envelope_db(samples, duration)
            dips = find_dips(env, frame)
            transcript: list[tuple[float, float, str]] = []
            try:
                from faster_whisper import WhisperModel
                _status("Transcribing locally for lyric anchors… "
                        "(first run downloads the model)")
                model = WhisperModel("small", device="cpu", compute_type="int8")
                segs, _info = model.transcribe(
                    str(path), language="en", beam_size=1,
                    condition_on_previous_text=False)
                for s in segs:
                    transcript.append((s.start, s.end, s.text))
                    _status(f"Transcribing… {_fmt_time(s.end)} / "
                            f"{_fmt_time(duration)}")
            except ImportError:
                _status("faster-whisper not installed — proposing from "
                        "envelope only (pip install faster-whisper for "
                        "lyric-guided proposals).")
            except Exception:
                pass  # transcription is best-effort; dips still work
            bounds = align_boundaries(setlist, dips, transcript, duration)
            self.after(0, lambda: self._apply_proposal(bounds))

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_proposal(self, bounds) -> None:
        self.btn_propose.configure(state="normal")
        if not bounds:
            self.status.configure(
                text="Could not propose splits — no usable gaps found. "
                     "Try Detect silences or place markers manually.")
            return
        for t in self._tracks:
            t.flagged = False
        self._tracks[0].start_sec = 0.0
        for b in bounds:
            idx = b.index + 1
            if idx < len(self._tracks):
                self._tracks[idx].start_sec = round(b.time, 2)
                self._tracks[idx].flagged = b.confidence < 0.4
        self._sort_tracks()
        self._sync_markers()
        self._refresh_tv()
        self._update_split_btn()
        n_low = sum(t.flagged for t in self._tracks)
        msg = f"Proposed {len(bounds)} boundaries."
        if n_low:
            msg += (f"  {n_low} low-confidence (⚠) — click each to audition, "
                    "then drag its marker.")
        self.status.configure(text=msg)

    def _run_silence_detect(self) -> None:
        if self._duration <= 0:
            return
        # Use the decoded waveform envelope — adaptive, instant, and far more
        # reliable for live shows than a fixed-dB ffmpeg silencedetect. Falls
        # back to ffmpeg only if the waveform hasn't decoded yet.
        ends = self.waveform.detect_quiet_boundaries()
        if ends:
            self._on_silences(ends, len(self._tracks))
            return
        if self.waveform._samples:
            self.status.configure(
                text="No clear gaps found. Try dragging markers, or lower the gap "
                     "threshold by adding splits manually.")
            return
        # Waveform still decoding → fall back to ffmpeg pass.
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            return
        self.btn_silence.configure(state="disabled")
        self.status.configure(text="Detecting silences…")
        n = len(self._tracks)

        def _worker():
            ends = _detect_silences(ffmpeg, self._audio_path)
            self.after(0, lambda: self._on_silences(ends, n))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_silences(self, ends: list[float], n_tracks: int) -> None:
        self.btn_silence.configure(state="normal")
        if not ends:
            self.status.configure(text="No clear gaps found — adjust times manually.")
            return
        if not self._tracks:
            # Build a fresh track list from the detected boundaries.
            self._tracks = [SplitTrack(i + 1, f"Track {i + 1:02d}", round(t, 2))
                            for i, t in enumerate([0.0] + ends)]
        else:
            self._apply_silence_boundaries(ends)
        self._sort_tracks()
        self._sync_markers()
        self._refresh_tv()
        self._update_split_btn()
        self.status.configure(
            text=f"Detected {len(ends)} gap(s) → {len(self._tracks)} track(s). "
                 "Drag markers to fine-tune; click a track to audition its start.")

    def _apply_silence_boundaries(self, ends: list[float]) -> None:
        n = len(self._tracks)
        if n < 2 or not ends:
            return
        used: set[int] = set()
        self._tracks[0].start_sec = 0.0
        for i in range(1, n):
            expected = self._duration * i / n
            best_j, best_dist = None, float("inf")
            for j, t in enumerate(ends):
                if j in used or t < self._tracks[i - 1].start_sec + 30:
                    continue
                d = abs(t - expected)
                if d < best_dist:
                    best_dist, best_j = d, j
            if best_j is not None:
                self._tracks[i].start_sec = round(ends[best_j], 2)
                used.add(best_j)

    # ── Waveform ↔ track list sync ────────────────────────────────────────────

    def _sync_markers(self) -> None:
        """Push current track start times to the waveform as markers."""
        times = [t.start_sec for t in self._tracks[1:]]  # exclude track 1 (always 0)
        self.waveform.set_markers(times)

    def _sort_tracks(self) -> None:
        """Keep tracks ordered by start time and renumbered 1..n, so a track's
        number always matches its position in the show (track 2 can never sit
        after track 3)."""
        self._tracks.sort(key=lambda t: t.start_sec)
        for i, t in enumerate(self._tracks, 1):
            t.number = i

    def _on_marker_moved(self, marker_idx: int, new_time: float) -> None:
        """Called by WaveformView when user drags a marker."""
        track_idx = marker_idx + 1  # marker 0 = track 2
        if track_idx < len(self._tracks):
            self._tracks[track_idx].start_sec = round(new_time, 2)
            self._tracks[track_idx].flagged = False  # human-adjusted → trusted
            self._sort_tracks()
            self._refresh_tv()
            # Reselect the track now at this start time
            for i, t in enumerate(self._tracks):
                if abs(t.start_sec - round(new_time, 2)) < 0.001:
                    children = self.tv.get_children()
                    if i < len(children):
                        self.tv.selection_set(children[i])
                        self.tv.see(children[i])
                    break

    def _on_waveform_seek(self, t: float) -> None:
        """Called when user clicks the waveform (sets cursor)."""
        self.waveform.lbl_time.configure(text=_fmt_time(t))

    # ── Track list UI ─────────────────────────────────────────────────────────

    def _refresh_tv(self) -> None:
        sel = self.tv.selection()
        sel_idx = self._iid_to_index(sel[0]) if sel else None
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        for t in self._tracks:
            start = _fmt_time(t.start_sec)
            if t.flagged:
                start = f"⚠ {start}"
            self.tv.insert("", "end", values=(t.number, start, t.title, t.disc))
        children = self.tv.get_children()
        if sel_idx is not None and sel_idx < len(children):
            self.tv.selection_set(children[sel_idx])

    def _on_row_select(self, _evt=None) -> None:
        # Fires on user AND programmatic selection — just populate the edit
        # fields and move the cursor (no playback, so marker drags don't play).
        sel = self.tv.selection()
        if not sel:
            return
        idx = self._iid_to_index(sel[0])
        if idx is None or idx >= len(self._tracks):
            return
        t = self._tracks[idx]
        self._loading_row = True
        try:
            self.var_start.set(_fmt_time(t.start_sec))
            self.var_title_edit.set(t.title)
            self.var_disc_edit.set(str(t.disc))
        finally:
            self._loading_row = False
        self.waveform.set_cursor(t.start_sec)

    def _on_row_click(self, event) -> None:
        # An actual mouse click on a track row → audition it: play from that
        # track's start marker. (ButtonRelease only fires on real clicks, not
        # on programmatic selection, so dragging a marker won't trigger this.)
        row = self.tv.identify_row(event.y)
        if not row:
            return
        idx = self._iid_to_index(row)
        if idx is None or idx >= len(self._tracks):
            return
        self.waveform.play_from(self._tracks[idx].start_sec)

    def _apply_edit(self) -> None:
        sel = self.tv.selection()
        if not sel:
            return
        idx = self._iid_to_index(sel[0])
        if idx is None or idx >= len(self._tracks):
            return
        t = self._tracks[idx]
        try:
            t.start_sec = _parse_time(self.var_start.get())
        except ValueError:
            messagebox.showerror("Show Splitter",
                                 f"Invalid time: '{self.var_start.get()}'\nUse  m:ss  or  h:mm:ss")
            return
        t.title = self.var_title_edit.get().strip() or t.title
        try:
            t.disc = max(1, int(self.var_disc_edit.get()))
        except ValueError:
            pass
        t.flagged = False     # human-adjusted → trusted
        self._sort_tracks()   # an edited start time may reorder the tracks
        self._refresh_tv()
        self._sync_markers()

    def _add_track(self) -> None:
        n = len(self._tracks)
        new_t = SplitTrack(n + 1,
                           f"Track {n + 1:02d}",
                           round(self._duration * n / (n + 1)) if self._duration and n else 0.0)
        self._tracks.append(new_t)
        self._renumber()
        self._refresh_tv()
        self._sync_markers()
        self._update_split_btn()

    def _remove_track(self) -> None:
        sel = self.tv.selection()
        if not sel:
            return
        idx = self._iid_to_index(sel[0])
        if idx is not None:
            self._tracks.pop(idx)
            self._renumber()
            self._refresh_tv()
            self._sync_markers()
            self._update_split_btn()

    def _move_up(self) -> None:
        sel = self.tv.selection()
        if not sel:
            return
        idx = self._iid_to_index(sel[0])
        if idx is None or idx == 0:
            return
        self._tracks[idx - 1], self._tracks[idx] = self._tracks[idx], self._tracks[idx - 1]
        self._renumber()
        self._refresh_tv()
        self._sync_markers()
        children = self.tv.get_children()
        if idx - 1 < len(children):
            self.tv.selection_set(children[idx - 1])

    def _move_down(self) -> None:
        sel = self.tv.selection()
        if not sel:
            return
        idx = self._iid_to_index(sel[0])
        if idx is None or idx >= len(self._tracks) - 1:
            return
        self._tracks[idx], self._tracks[idx + 1] = self._tracks[idx + 1], self._tracks[idx]
        self._renumber()
        self._refresh_tv()
        self._sync_markers()
        children = self.tv.get_children()
        if idx + 1 < len(children):
            self.tv.selection_set(children[idx + 1])

    def _renumber(self) -> None:
        for i, t in enumerate(self._tracks, 1):
            t.number = i

    def _iid_to_index(self, iid: str) -> Optional[int]:
        try:
            return list(self.tv.get_children()).index(iid)
        except ValueError:
            return None

    def _browse_outdir(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select output folder",
            initialdir=self.var_outdir.get() or None,
        )
        if d:
            self.var_outdir.set(d)

    def _browse_cover(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Select cover image",
            initialdir=(str(self._audio_path.parent) if self._audio_path else None),
            filetypes=[("Images", "*.jpg *.jpeg *.png"), ("All files", "*")])
        if f:
            self.var_cover.set(f)

    def _update_split_btn(self) -> None:
        ok = bool(self._audio_path and self._tracks and self.var_outdir.get())
        self.btn_split.configure(state="normal" if ok else "disabled")

    # ── Session save / load ────────────────────────────────────────────────────

    def _save_session(self) -> None:
        if not self._tracks and not self._audio_path:
            messagebox.showinfo("Show Splitter",
                                "Nothing to save yet — load a show and add some tracks.")
            return
        meta = {
            "artist":       self._field_vars["ARTIST"].get().strip(),
            "date":         self._field_vars["DATE"].get().strip(),
            "venue":        self._field_vars["VENUE"].get().strip(),
            "location":     self._field_vars["LOCATION"].get().strip(),
            "abbrev":       self._field_vars["ABBREV"].get().strip(),
            "source_chain": self._source_widget.get("1.0", "end").strip(),
            "format":       self.var_fmt.get(),
            "template":     self.var_nametpl.get().strip(),
        }
        tracks = [(t.start_sec, t.disc, t.title) for t in self._tracks]
        content = serialize_session(
            str(self._audio_path) if self._audio_path else "", meta, tracks)

        # Default filename from abbrev+date, next to the audio file.
        default = (f"{meta['abbrev']}{meta['date']}" if meta['abbrev'] and meta['date']
                   else (self._audio_path.stem if self._audio_path else "session"))
        initial_dir = str(self._audio_path.parent) if self._audio_path else None
        out = filedialog.asksaveasfilename(
            parent=self, title="Save split session",
            initialdir=initial_dir, initialfile=default + SESSION_EXT,
            defaultextension=SESSION_EXT,
            filetypes=_session_filetypes())
        if not out:
            return
        try:
            Path(out).write_text(content, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Show Splitter", f"Could not save:\n{e}")
            return
        self.status.configure(text=f"Saved session: {Path(out).name}")

    def _load_session(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Load split session",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=_session_filetypes())
        if not f:
            return
        try:
            data = parse_session(read_text_smart(Path(f)))
        except OSError as e:
            messagebox.showerror("Show Splitter", f"Could not read:\n{e}")
            return
        meta = data["meta"]
        # Restore fields
        self._field_vars["ARTIST"].set(meta.get("artist", ""))
        self._field_vars["DATE"].set(meta.get("date", ""))
        self._field_vars["VENUE"].set(meta.get("venue", ""))
        self._field_vars["LOCATION"].set(meta.get("location", ""))
        self._field_vars["ABBREV"].set(meta.get("abbrev", ""))
        self._source_widget.delete("1.0", "end")
        self._source_widget.insert("1.0", meta.get("source_chain", ""))
        if meta.get("format") in ("FLAC", "WAV", "MP3"):
            self.var_fmt.set(meta["format"])
        if meta.get("template"):
            self.var_nametpl.set(meta["template"])

        # Restore tracks
        n_sets = max((d for _, d, _ in data["tracks"]), default=1)
        self._tracks = [
            SplitTrack(number=i + 1, title=title, start_sec=round(start, 2),
                       disc=disc, disc_total=n_sets)
            for i, (start, disc, title) in enumerate(data["tracks"])
        ]
        self._sort_tracks()
        self._refresh_tv()
        self._sync_markers()
        self._update_split_btn()

        # Load the referenced audio file if it still exists.
        src = meta.get("source", "")
        if src and Path(src).exists():
            self._preserve_track_times = True   # don't even-split over restored times
            self._load_audio(Path(src))
            self.status.configure(text=f"Loaded session: {Path(f).name}")
        else:
            self.status.configure(
                text=f"Loaded session: {Path(f).name}  "
                     "(audio file missing — use Load show file… to relink)")

    # ── Split & Tag ───────────────────────────────────────────────────────────

    def _split_and_tag(self) -> None:
        if not self._audio_path or not self._tracks:
            return
        outdir = Path(self.var_outdir.get())
        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Show Splitter", f"Cannot create folder:\n{e}")
            return
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror("Show Splitter",
                                 "ffmpeg not found. Install via Tools → Update all CLI tools.")
            return
        self.btn_split.configure(state="disabled")
        self.progress.configure(maximum=len(self._tracks), value=0)
        fields = {
            "ARTIST":   self._field_vars["ARTIST"].get().strip(),
            "DATE":     self._field_vars["DATE"].get().strip(),
            "VENUE":    self._field_vars["VENUE"].get().strip(),
            "LOCATION": self._field_vars["LOCATION"].get().strip(),
            "SOURCE":   self._source_widget.get("1.0", "end").strip(),
            "ABBREV":   self._field_vars["ABBREV"].get().strip(),
        }
        template = self.var_nametpl.get().strip() or DEFAULT_NAME_TEMPLATE
        # Remember the template + tracker for next time.
        self.config_obj["splitter_name_template"] = template
        if self.var_tracker.get().strip():
            self.config_obj["torrent_default_tracker_url"] = self.var_tracker.get().strip()
        self.config_obj.save()
        pkg = {
            "txt":     self.var_pkg_txt.get(),
            "ffp":     self.var_pkg_ffp.get(),
            "md5":     self.var_pkg_md5.get(),
            "embed":   self.var_pkg_embed.get(),
            "torrent": self.var_pkg_torrent.get(),
            "cover":   self.var_cover.get().strip(),
            "tracker": self.var_tracker.get().strip(),
        }
        if pkg["torrent"] and not pkg["tracker"]:
            messagebox.showerror("Show Splitter",
                                 "Enter a tracker URL to create a torrent, or uncheck Torrent.")
            self.btn_split.configure(state="normal")
            return
        # Remember for a post-package LMA upload.
        self._pkg_outdir = outdir
        self._pkg_fields = fields
        threading.Thread(
            target=self._worker,
            args=(ffmpeg, list(self._tracks), fields, template,
                  self.var_fmt.get(), outdir, self._duration, pkg),
            daemon=True,
        ).start()

    def _worker(self, ffmpeg: Path, tracks: list[SplitTrack],
                fields: dict, template: str, fmt: str, outdir: Path,
                total_dur: float, pkg: dict) -> None:
        ext = {"FLAC": ".flac", "WAV": ".wav", "MP3": ".mp3"}.get(fmt, ".flac")
        src_info = detect_source(fields.get("SOURCE", ""))
        src_label = src_info.label()
        # Album = "DATE Venue, Location"  (e.g. "2026-05-24 HopMonk, Novato, CA")
        place = ", ".join(v for v in [fields.get("VENUE"), fields.get("LOCATION")] if v)
        album = " ".join(v for v in [fields.get("DATE"), place] if v).strip()
        if src_label:
            album = f"{album} {src_label}".strip()

        abbrev = fields.get("ABBREV") or _guess_abbrev(fields.get("ARTIST", ""))
        date_str = fields.get("DATE", "")

        disc_totals: dict[int, int] = {}
        for t in tracks:
            disc_totals[t.disc] = disc_totals.get(t.disc, 0) + 1
        n = len(tracks)
        # Per-disc running track number for the eTree d{D}t{n} filename.
        disc_counter: dict[int, int] = {}
        ok, errors = 0, []
        written: list[Path] = []   # output files, for checksum/cover/torrent

        for i, track in enumerate(tracks):
            disc_counter[track.disc] = disc_counter.get(track.disc, 0) + 1
            track_in_disc = disc_counter[track.disc]
            fname = _apply_name_template(
                template, abbrev, date_str, track.disc, track_in_disc,
                _sanitize(track.title), ext)
            out   = outdir / fname
            start = track.start_sec
            end   = tracks[i + 1].start_sec if i + 1 < n else None
            if end is not None and end <= start:
                end = None
            self.after(0, lambda lbl=f"[{i+1}/{n}] {fname}":
                       self.status.configure(text=lbl))
            # Split
            cmd = [str(ffmpeg), "-y", "-hide_banner",
                   "-ss", str(start)]
            if end is not None:
                cmd += ["-to", str(end)]
            cmd += ["-i", str(self._audio_path)]
            if fmt == "FLAC":
                cmd += ["-c:a", "flac", "-compression_level", "8"]
            elif fmt == "MP3":
                cmd += ["-c:a", "libmp3lame", "-q:a", "0"]
            else:
                cmd += ["-c:a", "pcm_s16le"]
            cmd.append(str(out))
            r = subprocess.run(cmd, capture_output=True, timeout=600,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
                errors.append(f"{fname}: {err or 'ffmpeg error'}")
                self.after(0, lambda v=i+1: self.progress.configure(value=v))
                continue
            # Tag
            tag_dict = {
                "ARTIST":      fields.get("ARTIST", ""),
                "ALBUMARTIST": fields.get("ARTIST", ""),
                "ALBUM":       album or fields.get("ARTIST", ""),
                "DATE":        fields.get("DATE", ""),
                "VENUE":       fields.get("VENUE", ""),
                "LOCATION":    fields.get("LOCATION", ""),
                "SOURCE":      fields.get("SOURCE", ""),
                "TITLE":       track.title,
                "TRACKNUMBER": f"{track_in_disc:02d}",
                "TRACKTOTAL":  str(disc_totals.get(track.disc, n)),
                "TOTALTRACKS": str(disc_totals.get(track.disc, n)),
            }
            if track.disc_total > 1:
                tag_dict["DISCNUMBER"] = str(track.disc)
                tag_dict["DISCTOTAL"]  = str(track.disc_total)
                tag_dict["TOTALDISCS"] = str(track.disc_total)
                tag_dict["TRACKTOTAL"] = str(disc_totals.get(track.disc, n))
                tag_dict["TOTALTRACKS"] = tag_dict["TRACKTOTAL"]
            tag_dict = {k: v for k, v in tag_dict.items() if v}
            if mutagen_available():
                try:
                    mutagen_write_tags(out, tag_dict)
                except Exception as e:
                    errors.append(f"{fname} [tag]: {e}")
            written.append(out)
            ok += 1
            self.after(0, lambda v=i+1: self.progress.configure(value=v))

        # ── Packaging: cover, checksum, setlist .txt, torrent ────────────────
        extras = self._package(outdir, written, fields, fmt, album, pkg, errors)

        self.after(0, lambda: self._done(ok, errors, extras))

    def _package(self, outdir: Path, written: list, fields: dict, fmt: str,
                 album: str, pkg: dict, errors: list) -> list[str]:
        """Post-split steps that build the rest of the share-ready folder.
        Returns a list of created extra filenames for the summary."""
        extras: list[str] = []
        if not written:
            return extras
        folder_name = outdir.name

        def _status(msg):
            self.after(0, lambda: self.status.configure(text=msg))

        # 1) Cover art → copy into the folder (cover.jpg) and embed in FLACs.
        cover = pkg.get("cover")
        if cover and Path(cover).exists():
            _status("Adding cover art…")
            try:
                import shutil
                dest = outdir / ("cover" + Path(cover).suffix.lower())
                if Path(cover).resolve() != dest.resolve():
                    shutil.copy2(cover, dest)
                extras.append(dest.name)
                if pkg.get("embed") and fmt == "FLAC":
                    metaflac = get_tool("metaflac").path(self.config_obj)
                    if metaflac.exists():
                        # Bare filename → metaflac auto-detects MIME/resolution
                        # and defaults to type 3 (front cover).
                        for fp in written:
                            subprocess.run(
                                [str(metaflac), f"--import-picture-from={dest}", str(fp)],
                                capture_output=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as e:
                errors.append(f"cover: {e}")

        # 2) Setlist / info .txt named after the folder.
        if pkg.get("txt"):
            _status("Writing setlist .txt…")
            try:
                show = EtreeShow(
                    artist=fields.get("ARTIST", ""), date=fields.get("DATE", ""),
                    venue=fields.get("VENUE", ""), location=fields.get("LOCATION", ""),
                    source=fields.get("SOURCE", ""))
                # Rebuild sets/tracks from the current track list.
                from .live_tagger import EtreeSet, EtreeTrack
                by_disc: dict[int, EtreeSet] = {}
                gi = 0
                for t in self._tracks:
                    gi += 1
                    s = by_disc.get(t.disc)
                    if s is None:
                        s = EtreeSet(label=f"Set {t.disc}")
                        by_disc[t.disc] = s
                        show.sets.append(s)
                    s.tracks.append(EtreeTrack(global_index=gi,
                                               set_index=len(s.tracks) + 1,
                                               title=t.title))
                txt_path = outdir / f"{folder_name}.txt"
                txt_path.write_text(generate_etree_file(show), encoding="utf-8")
                extras.append(txt_path.name)
            except Exception as e:
                errors.append(f"setlist txt: {e}")

        # 3) Checksums (FFP for FLAC, MD5 for any).
        if pkg.get("ffp") and fmt == "FLAC":
            _status("Creating FFP checksum…")
            try:
                from . import checksums as _ck
                metaflac = get_tool("metaflac").path(self.config_obj)
                entries = [(fp.name, _ck.flac_fingerprint(fp, metaflac)) for fp in written]
                ffp_path = outdir / f"{folder_name}.ffp"
                _ck.write_ffp(entries, ffp_path)
                extras.append(ffp_path.name)
            except Exception as e:
                errors.append(f"ffp: {e}")
        if pkg.get("md5"):
            _status("Creating MD5 checksum…")
            try:
                from . import checksums as _ck
                entries = [(fp.name, _ck.md5sum(fp)) for fp in written]
                md5_path = outdir / f"{folder_name}.md5"
                _ck.write_md5(entries, md5_path)
                extras.append(md5_path.name)
            except Exception as e:
                errors.append(f"md5: {e}")

        # 4) Torrent of the finished folder (written to the PARENT so it isn't
        #    hashed into itself).
        if pkg.get("torrent") and pkg.get("tracker"):
            _status("Creating torrent (hashing folder)…")
            try:
                from . import torrent as _tor
                data = _tor.create_torrent(
                    outdir, [pkg["tracker"]], private=True,
                    comment=album or folder_name)
                tor_path = outdir.parent / f"{folder_name}.torrent"
                tor_path.write_bytes(data)
                extras.append(f"../{tor_path.name}")
            except Exception as e:
                errors.append(f"torrent: {e}")

        return extras

    def _done(self, ok: int, errors: list[str], extras: list[str] = None) -> None:
        extras = extras or []
        self.btn_split.configure(state="normal")
        extra_line = ("\n\nAlso created: " + ", ".join(extras)) if extras else ""
        if errors:
            messagebox.showerror(
                "Show Splitter",
                f"Split {ok} track(s). {len(errors)} issue(s):\n\n"
                + "\n".join(errors[:10]) + extra_line)
        else:
            messagebox.showinfo(
                "Show Splitter",
                f"Split and tagged {ok} track(s) successfully.{extra_line}")
        self.status.configure(
            text=f"Done. {ok} tracks + {len(extras)} extra file(s), "
                 f"{len(errors)} issue(s).")
        self.progress.configure(value=0)
        # Optional: hand the finished folder to the Archive.org upload dialog.
        if ok > 0 and getattr(self, "var_pkg_lma", None) and self.var_pkg_lma.get():
            self._open_lma_upload()

    def _open_lma_upload(self) -> None:
        from .lma_upload import LmaUploadDialog
        outdir = getattr(self, "_pkg_outdir", None) or Path(self.var_outdir.get())
        fields = getattr(self, "_pkg_fields", {})
        # Build a setlist description from the current track list.
        lines = []
        for t in self._tracks:
            lines.append(f"{t.number}. {t.title}")
        meta = {
            "artist":   fields.get("ARTIST", self._field_vars["ARTIST"].get().strip()),
            "date":     fields.get("DATE", self._field_vars["DATE"].get().strip()),
            "venue":    fields.get("VENUE", self._field_vars["VENUE"].get().strip()),
            "location": fields.get("LOCATION", self._field_vars["LOCATION"].get().strip()),
            "source":   fields.get("SOURCE", self._source_widget.get("1.0", "end").strip()),
            "abbrev":   self._field_vars["ABBREV"].get().strip(),
            "description": "\n".join(lines),
        }
        LmaUploadDialog(self, self.config_obj, self.runner, folder=outdir, meta=meta)
