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

from .action_picker import AUDIO_EXTS
from .live_tagger import EtreeShow, parse_etree_file
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
        self._play_proc: Optional[subprocess.Popen] = None
        self._play_after: Optional[str] = None
        self._play_wall = 0.0
        self._play_audio = 0.0
        self._preview_tmp: Optional[str] = None
        self.on_marker_moved = on_marker_moved
        self.on_seek = on_seek
        self._build()

    def _build(self) -> None:
        # Transport bar
        tr = tk.Frame(self, bg=self.BG_COLOR)
        tr.pack(fill="x", side="bottom", padx=4, pady=(0, 2))
        self.btn_play = tk.Button(tr, text="▶ Play", width=8,
                                  command=self._play, state="disabled",
                                  bg="#2a2a3a", fg="#dddddd",
                                  activebackground="#3a3a4a",
                                  relief="flat", padx=6)
        self.btn_play.pack(side="left", padx=(0, 2))
        self.btn_stop = tk.Button(tr, text="⏹", width=4,
                                  command=self._stop, state="disabled",
                                  bg="#2a2a3a", fg="#dddddd",
                                  activebackground="#3a3a4a",
                                  relief="flat")
        self.btn_stop.pack(side="left", padx=(0, 8))
        self.lbl_time = tk.Label(tr, text="0:00:00", fg="#cccccc",
                                 bg=self.BG_COLOR, font=("Consolas", 10),
                                 width=10, anchor="w")
        self.lbl_time.pack(side="left")
        for label, cmd in (("Fit", self._zoom_fit),
                            ("−",   self._zoom_out),
                            ("+",   self._zoom_in)):
            b = tk.Button(tr, text=label, command=cmd, state="disabled",
                          bg="#2a2a3a", fg="#dddddd",
                          activebackground="#3a3a4a",
                          relief="flat", width=4 if label == "Fit" else 3)
            b.pack(side="right", padx=1)
            setattr(self, f"_btn_{label}", b)

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
        self.canvas.bind("<MouseWheel>",      self._on_wheel)
        self.canvas.bind("<Button-4>",        self._on_wheel)
        self.canvas.bind("<Button-5>",        self._on_wheel)

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
        self._zoom = max(1.0, self._canvas_w() / duration) if duration else 1.0
        for w in [self.btn_play, self.btn_stop,
                  self._btn_Fit, self._btn_["−"], self._btn_["+"]]:
            pass  # buttons stay disabled until decode done
        self._set_controls("disabled")
        self._show_message("Decoding waveform…  (this may take a moment for large files)")
        if status_cb:
            status_cb("Decoding waveform…")

        def _worker():
            samp = self._decode(path, ffmpeg)
            self.after(0, lambda: self._on_loaded(samp, status_cb))

        threading.Thread(target=_worker, daemon=True).start()

    def set_markers(self, times: list[float]) -> None:
        self._markers = sorted(t for t in times if t > 0.0)
        self._redraw()

    def set_cursor(self, t: float) -> None:
        self._cursor = max(0.0, min(t, self._duration))
        self.lbl_time.configure(text=_fmt_time(self._cursor))
        self._redraw()

    # ── Decode ────────────────────────────────────────────────────────────────

    def _decode(self, path: Path, ffmpeg: Path) -> list[float]:
        cmd = [str(ffmpeg), "-hide_banner", "-i", str(path),
               "-af", f"aresample={self._decode_rate}",
               "-ac", "1", "-f", "s16le", "-"]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            raw = proc.stdout.read()
            proc.wait()
        except Exception:
            return []
        buf = array.array("h")
        try:
            buf.frombytes(raw[:len(raw) - len(raw) % 2])
        except Exception:
            return []
        if not buf:
            return []
        # Subsample to at most 8 M values to keep memory reasonable
        step = max(1, len(buf) // 8_000_000)
        if step > 1:
            buf = array.array("h", (buf[i] for i in range(0, len(buf), step)))
        peak = max(abs(v) for v in buf) or 1
        return [abs(v) / peak for v in buf]

    def _on_loaded(self, samples: list[float], status_cb=None) -> None:
        self._samples = samples
        self._set_controls("normal")
        self._zoom_fit()
        if status_cb:
            label = f"Waveform loaded  ({len(samples):,} samples)"
            status_cb(label)

    def _set_controls(self, state: str) -> None:
        play_state = state if self._samples or state == "disabled" else "disabled"
        self.btn_play.configure(state=play_state)
        for attr in ("_btn_Fit", "_btn_−", "_btn_+"):
            w = getattr(self, attr, None)
            if w:
                w.configure(state=state)

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
        if not self._samples and self._duration == 0.0:
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
                poly = pts_top + list(reversed(pts_bot))
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

    def _on_wheel(self, event) -> None:
        if event.num == 4 or (hasattr(event, "delta") and event.delta > 0):
            factor = 1.3
        else:
            factor = 1 / 1.3
        self._zoom_around(factor, self._x_to_sec(event.x))

    # ── Mouse interaction ─────────────────────────────────────────────────────

    def _nearest_marker(self, x: float) -> Optional[int]:
        for i, t in enumerate(self._markers):
            if abs(self._sec_to_x(t) - x) <= self.HANDLE_R * 2:
                return i
        return None

    def _on_press(self, event) -> None:
        idx = self._nearest_marker(event.x)
        if idx is not None:
            self._drag_idx = idx
            self.canvas.config(cursor="sb_h_double_arrow")
        else:
            self._cursor = max(0.0, min(self._x_to_sec(event.x), self._duration))
            self.lbl_time.configure(text=_fmt_time(self._cursor))
            self._redraw()
            if self.on_seek:
                self.on_seek(self._cursor)

    def _on_drag(self, event) -> None:
        if self._drag_idx is None:
            return
        new_t = max(0.1, min(self._x_to_sec(event.x), self._duration - 0.1))
        i = self._drag_idx
        if i > 0:
            new_t = max(new_t, self._markers[i - 1] + 1.0)
        if i < len(self._markers) - 1:
            new_t = min(new_t, self._markers[i + 1] - 1.0)
        self._markers[i] = new_t
        self.lbl_time.configure(text=_fmt_time(new_t))
        self._redraw()

    def _on_release(self, event) -> None:
        if self._drag_idx is not None:
            if self.on_marker_moved:
                self.on_marker_moved(self._drag_idx, self._markers[self._drag_idx])
            self._drag_idx = None
            self.canvas.config(cursor="crosshair")

    # ── Playback ──────────────────────────────────────────────────────────────

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

    def _launch_player(self) -> None:
        import sys
        tmp = self._preview_tmp
        if not tmp or not Path(tmp).exists():
            self.btn_play.configure(state="normal")
            return
        if sys.platform == "darwin":
            cmd = ["afplay", tmp]
        else:
            # Try ffplay, fall back to aplay (Linux)
            ffplay = (self._ffmpeg_path.parent / "ffplay" if self._ffmpeg_path else None)
            if ffplay and ffplay.exists():
                cmd = [str(ffplay), "-nodisp", "-autoexit", tmp]
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

        if initial_files:
            audio = [f for f in initial_files
                     if Path(f).suffix.lower() in AUDIO_EXTS]
            if audio:
                self.after(80, lambda: self._load_audio(Path(audio[0])))

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
        ttk.Button(bar, text="Even split", command=self._even_split).pack(side="left", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(bar, text="Add track",  command=self._add_track).pack(side="left")
        ttk.Button(bar, text="Remove",     command=self._remove_track).pack(side="left", padx=4)
        ttk.Button(bar, text="↑",  width=3, command=self._move_up).pack(side="left")
        ttk.Button(bar, text="↓",  width=3, command=self._move_down).pack(side="left", padx=2)

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
        ]):
            ttk.Label(left, text=f"{label}:").grid(row=row_i, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self._field_vars[key] = var
            ttk.Entry(left, textvariable=var).grid(row=row_i, column=1,
                                                    sticky="ew", padx=(6, 0), pady=2)
        ttk.Label(left, text="Source:").grid(row=4, column=0, sticky="nw", pady=2)
        self._source_widget = tk.Text(left, height=3, font=("Segoe UI", 9), wrap="word")
        self._source_widget.grid(row=4, column=1, sticky="ew", padx=(6, 0), pady=2)
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
        self.btn_split = ttk.Button(bar, text="Split & Tag",
                                    command=self._split_and_tag, state="disabled")
        self.btn_split.pack(side="right", padx=4)
        self.progress = ttk.Progressbar(bar, mode="determinate", length=180)
        self.progress.pack(side="right", padx=(0, 8))

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
        self.waveform.load(path, ffmpeg, dur,
                           status_cb=lambda msg: self.status.configure(text=msg))
        if self._tracks:
            self._even_split()
        self._update_split_btn()

    # ── Setlist loading ───────────────────────────────────────────────────────

    def _load_txt_dialog(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Select eTree text file",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if f:
            self._load_txt_path(Path(f))

    def _load_txt_path(self, p: Path) -> None:
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = p.read_text(encoding="latin-1")
        except OSError as e:
            messagebox.showerror("Show Splitter", f"Cannot read {p.name}:\n{e}")
            return
        show = parse_etree_file(content)
        self._show = show
        self._field_vars["ARTIST"].set(show.artist)
        self._field_vars["DATE"].set(show.date)
        self._field_vars["VENUE"].set(show.venue)
        self._field_vars["LOCATION"].set(show.location)
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

    def _run_silence_detect(self) -> None:
        if not self._audio_path or self._duration <= 0:
            return
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror("Show Splitter",
                                 "ffmpeg not found. Install via Tools → Update all CLI tools.")
            return
        self.btn_silence.configure(state="disabled")
        self.status.configure(text="Detecting silences… (may take a minute)")
        n = len(self._tracks)

        def _worker():
            ends = _detect_silences(ffmpeg, self._audio_path)
            self.after(0, lambda: self._on_silences(ends, n))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_silences(self, ends: list[float], n_tracks: int) -> None:
        self.btn_silence.configure(state="normal")
        if not ends:
            self.status.configure(text="No silences detected — adjust times manually.")
            return
        if not self._tracks:
            self._tracks = [SplitTrack(i + 1, f"Track {i + 1:02d}", round(t, 2))
                            for i, t in enumerate([0.0] + ends)]
            self._refresh_tv()
            self._sync_markers()
            self._update_split_btn()
            self.status.configure(text=f"Created {len(self._tracks)} tracks from silences.")
            return
        self._apply_silence_boundaries(ends)
        self._sync_markers()
        self._refresh_tv()
        self.status.configure(
            text=f"Applied {len(ends)} silence boundaries to {len(self._tracks)} tracks.")

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

    def _on_marker_moved(self, marker_idx: int, new_time: float) -> None:
        """Called by WaveformView when user drags a marker."""
        track_idx = marker_idx + 1  # marker 0 = track 2
        if track_idx < len(self._tracks):
            self._tracks[track_idx].start_sec = round(new_time, 2)
            self._refresh_tv()
            # Select that row
            children = self.tv.get_children()
            if track_idx < len(children):
                self.tv.selection_set(children[track_idx])
                self.tv.see(children[track_idx])

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
            self.tv.insert("", "end", values=(t.number, _fmt_time(t.start_sec),
                                               t.title, t.disc))
        children = self.tv.get_children()
        if sel_idx is not None and sel_idx < len(children):
            self.tv.selection_set(children[sel_idx])

    def _on_row_select(self, _evt=None) -> None:
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
        # Scroll waveform to show this track's start
        self.waveform.set_cursor(t.start_sec)

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

    def _update_split_btn(self) -> None:
        ok = bool(self._audio_path and self._tracks and self.var_outdir.get())
        self.btn_split.configure(state="normal" if ok else "disabled")

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
        }
        threading.Thread(
            target=self._worker,
            args=(ffmpeg, list(self._tracks), fields,
                  self.var_fmt.get(), outdir, self._duration),
            daemon=True,
        ).start()

    def _worker(self, ffmpeg: Path, tracks: list[SplitTrack],
                fields: dict, fmt: str, outdir: Path, total_dur: float) -> None:
        ext = {"FLAC": ".flac", "WAV": ".wav", "MP3": ".mp3"}.get(fmt, ".flac")
        src_info = detect_source(fields.get("SOURCE", ""))
        src_label = src_info.label()
        album_parts = [v for v in [fields.get("DATE"), fields.get("VENUE"),
                                    fields.get("LOCATION")] if v]
        album = " ".join(album_parts[:2])  # Date + Venue
        if src_label:
            album = f"{album} {src_label}".strip()

        disc_totals: dict[int, int] = {}
        for t in tracks:
            disc_totals[t.disc] = disc_totals.get(t.disc, 0) + 1
        n = len(tracks)
        ok, errors = 0, []

        for i, track in enumerate(tracks):
            fname = f"{track.number:02d} - {_sanitize(track.title)}{ext}"
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
                "TRACKNUMBER": f"{track.number:02d}",
                "TRACKTOTAL":  str(n),
                "TOTALTRACKS": str(n),
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
            ok += 1
            self.after(0, lambda v=i+1: self.progress.configure(value=v))

        self.after(0, lambda: self._done(ok, errors))

    def _done(self, ok: int, errors: list[str]) -> None:
        self.btn_split.configure(state="normal")
        if errors:
            messagebox.showerror(
                "Show Splitter",
                f"Split {ok} track(s). {len(errors)} failed:\n\n" + "\n".join(errors[:10]))
        else:
            messagebox.showinfo("Show Splitter",
                                f"Split and tagged {ok} track(s) successfully.")
        self.status.configure(text=f"Done. {ok} tracks written, {len(errors)} failed.")
        self.progress.configure(value=0)
