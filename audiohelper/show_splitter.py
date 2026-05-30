"""Show Splitter — split a full-show WAV or FLAC into individual tracks.

Workflow:
  1. Load a single audio file (WAV / FLAC / any ffmpeg-readable format).
  2. Load a setlist text file (eTree format) to get track titles.
  3. Detect silences to propose split points, or use even spacing.
  4. Edit start times and titles in the track list.
  5. "Split & Tag" — writes individual files and applies all metadata.

Tag writing uses mutagen when available (FLAC, MP3, WAV, M4A, Ogg/Opus).
"""

import json
import os
import re
import subprocess
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .action_picker import AUDIO_EXTS
from .live_tagger import EtreeShow, parse_etree_file
from .tc_sources import detect_source
from .tc_tagger import mutagen_available, write_tags
from .tools import get_tool


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class SplitTrack:
    number: int
    title: str
    start_sec: float          # seconds from start of full recording
    disc: int = 1
    disc_total: int = 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(secs: float) -> str:
    """4623.5 → '1:17:03.5'"""
    total_s = int(secs)
    frac = secs - total_s
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    frac_str = f".{round(frac * 10):01d}" if frac >= 0.05 else ""
    return f"{h}:{m:02d}:{s:02d}{frac_str}" if h else f"{m}:{s:02d}{frac_str}"


def _parse_time(s: str) -> float:
    """'1:17:03.5' or '4:23' → seconds, or raise ValueError."""
    s = s.strip()
    parts = s.split(":")
    if len(parts) == 3:
        h, m, sec = parts
        return int(h) * 3600 + int(m) * 60 + float(sec)
    if len(parts) == 2:
        m, sec = parts
        return int(m) * 60 + float(sec)
    return float(s)


def _probe_audio(ffprobe: Path, path: Path) -> dict:
    """Return ffprobe JSON for *path*, or {} on failure."""
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


def _duration_from_probe(probe: dict) -> float:
    """Extract duration in seconds from ffprobe output."""
    try:
        return float(probe.get("format", {}).get("duration", 0))
    except (TypeError, ValueError):
        return 0.0


def _detect_silences(ffmpeg: Path, path: Path,
                     noise_db: float = -40.0, min_dur: float = 0.4) -> list[float]:
    """Run ffmpeg silencedetect and return a list of silence-end timestamps.

    Each value is the moment when audio *resumes* — i.e. the likely start of
    the next track.
    """
    cmd = [
        str(ffmpeg), "-i", str(path),
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
        "-f", "null", "-",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stderr = r.stderr or ""
    except Exception:
        return []

    ends: list[float] = []
    for line in stderr.splitlines():
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m:
            ends.append(float(m.group(1)))
    return sorted(ends)


def _sanitize_filename(name: str) -> str:
    """Strip characters that are illegal in filenames on Windows/Mac."""
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "untitled"


def _build_output_ext(fmt: str) -> str:
    return {"FLAC": ".flac", "WAV": ".wav", "MP3": ".mp3"}.get(fmt, ".flac")


def _split_one(ffmpeg: Path, src: Path, out: Path,
               start: float, end: Optional[float],
               fmt: str) -> None:
    """Extract one segment from *src* into *out*."""
    cmd = [str(ffmpeg), "-y", "-hide_banner"]
    cmd += ["-ss", str(start)]
    if end is not None:
        cmd += ["-to", str(end)]
    cmd += ["-i", str(src)]
    if fmt == "FLAC":
        cmd += ["-c:a", "flac", "-compression_level", "8"]
    elif fmt == "MP3":
        cmd += ["-c:a", "libmp3lame", "-q:a", "0"]
    else:  # WAV
        cmd += ["-c:a", "pcm_s16le"]
    cmd.append(str(out))
    r = subprocess.run(
        cmd, capture_output=True, timeout=600,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"ffmpeg exited {r.returncode}")


# ── Dialog ────────────────────────────────────────────────────────────────────

class ShowSplitterDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Show Splitter")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("1100x700")
        self.minsize(860, 540)

        # ── State ────────────────────────────────────────────────────────────
        self._audio_path: Optional[Path] = None
        self._duration: float = 0.0
        self._probe_data: dict = {}
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
                self.after(60, lambda: self._load_audio(Path(audio[0])))

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 0))
        bar.pack(fill="x")

        ttk.Button(bar, text="Load show file…",  command=self._browse_audio).pack(side="left")
        ttk.Button(bar, text="Load setlist…",    command=self._load_txt_dialog).pack(side="left", padx=4)
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

        paned = ttk.PanedWindow(body, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # ── Left: show data ────────────────────────────────────────────────
        left = ttk.Frame(paned, padding=(0, 0, 8, 0))

        # File info
        fi = ttk.LabelFrame(left, text="Audio file", padding=6)
        fi.pack(fill="x")
        self.lbl_file = ttk.Label(fi, text="(none)", foreground="gray",
                                  relief="sunken", padding=(4, 1))
        self.lbl_file.pack(fill="x")
        self.lbl_info = ttk.Label(fi, text="", foreground="gray")
        self.lbl_info.pack(anchor="w", pady=(2, 0))

        # Show data
        sd = ttk.LabelFrame(left, text="Show data", padding=8)
        sd.pack(fill="x", pady=(8, 0))
        sd.columnconfigure(1, weight=1)

        self._field_vars: dict[str, tk.StringVar] = {}
        for row_i, (label, key) in enumerate([
            ("Artist",   "ARTIST"),
            ("Date",     "DATE"),
            ("Venue",    "VENUE"),
            ("Location", "LOCATION"),
        ]):
            ttk.Label(sd, text=f"{label}:").grid(row=row_i, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self._field_vars[key] = var
            ttk.Entry(sd, textvariable=var).grid(
                row=row_i, column=1, sticky="ew", padx=(6, 0), pady=2)

        row_src = len(self._field_vars)
        ttk.Label(sd, text="Source:").grid(row=row_src, column=0, sticky="nw", pady=2)
        self._source_widget = tk.Text(sd, height=3, font=("Segoe UI", 9), wrap="word")
        self._source_widget.grid(row=row_src, column=1, sticky="ew", padx=(6, 0), pady=2)

        paned.add(left, weight=1)

        # ── Right: track list + edit row ───────────────────────────────────
        right = ttk.Frame(paned)

        tl = ttk.LabelFrame(right, text="Track list", padding=4)
        tl.pack(fill="both", expand=True)

        cols = ("num", "start", "title", "disc")
        self.tv = ttk.Treeview(tl, columns=cols, show="headings", selectmode="browse")
        self.tv.heading("num",   text="#",     anchor="center")
        self.tv.heading("start", text="Start", anchor="w")
        self.tv.heading("title", text="Title", anchor="w")
        self.tv.heading("disc",  text="Set",   anchor="center")
        self.tv.column("num",   width=34,  minwidth=28,  stretch=False, anchor="center")
        self.tv.column("start", width=90,  minwidth=70,  stretch=False, anchor="w")
        self.tv.column("title", width=300, minwidth=100, stretch=True,  anchor="w")
        self.tv.column("disc",  width=40,  minwidth=34,  stretch=False, anchor="center")
        tv_sb = ttk.Scrollbar(tl, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=tv_sb.set)
        tv_sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<<TreeviewSelect>>", self._on_row_select)

        # Inline edit strip
        edit = ttk.LabelFrame(right, text="Edit selected track", padding=6)
        edit.pack(fill="x", pady=(4, 0))
        edit.columnconfigure(1, weight=1)

        ttk.Label(edit, text="Start (m:ss):").grid(row=0, column=0, sticky="w")
        self.var_start = tk.StringVar()
        ttk.Entry(edit, textvariable=self.var_start, width=12).grid(
            row=0, column=1, sticky="w", padx=(6, 0))

        ttk.Label(edit, text="Title:").grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.var_title_edit = tk.StringVar()
        ttk.Entry(edit, textvariable=self.var_title_edit).grid(
            row=0, column=3, sticky="ew", padx=(6, 0))

        ttk.Label(edit, text="Set #:").grid(row=0, column=4, sticky="w", padx=(16, 0))
        self.var_disc_edit = tk.StringVar()
        ttk.Entry(edit, textvariable=self.var_disc_edit, width=5).grid(
            row=0, column=5, sticky="w", padx=(6, 0))

        ttk.Button(edit, text="Apply", command=self._apply_edit).grid(
            row=0, column=6, padx=(12, 0))
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
        self.btn_split = ttk.Button(bar, text="Split & Tag", command=self._split_and_tag,
                                    state="disabled")
        self.btn_split.pack(side="right", padx=4)

        self.progress = ttk.Progressbar(bar, mode="determinate", length=200)
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
        if not ffprobe.exists():
            messagebox.showerror("Show Splitter",
                                 "ffprobe not found. Install via Tools → Update all CLI tools.")
            return

        self._audio_path = path
        self.config_obj["last_input_dir"] = str(path.parent)
        self.config_obj.save()

        self.lbl_file.configure(text=path.name, foreground="black")
        self.status.configure(text="Probing file…")

        def _worker():
            probe = _probe_audio(ffprobe, path)
            dur = _duration_from_probe(probe)
            self.after(0, lambda: self._on_probe_done(probe, dur))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_probe_done(self, probe: dict, dur: float) -> None:
        self._probe_data = probe
        self._duration = dur

        # Build info line from ffprobe
        fmt_name = probe.get("format", {}).get("format_long_name", "")
        streams = probe.get("streams", [{}])
        audio_s = next((s for s in streams if s.get("codec_type") == "audio"), {})
        sr = audio_s.get("sample_rate", "")
        bits = audio_s.get("bits_per_sample") or audio_s.get("bits_per_raw_sample", "")
        ch = audio_s.get("channels", "")
        info_parts = []
        if dur:
            h, rem = divmod(int(dur), 3600)
            m, s = divmod(rem, 60)
            info_parts.append(f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}")
        if sr:
            info_parts.append(f"{int(sr):,} Hz")
        if bits:
            info_parts.append(f"{bits}-bit")
        if ch:
            info_parts.append(f"{'stereo' if ch == 2 else str(ch) + 'ch'}")
        self.lbl_info.configure(text="  •  ".join(info_parts) or fmt_name, foreground="")

        self.btn_silence.configure(state="normal" if self._duration > 0 else "disabled")
        self._update_split_btn()

        # Default output dir = same folder as source
        if self._audio_path and not self.var_outdir.get():
            self.var_outdir.set(str(self._audio_path.parent))

        # Re-apply even splits if we already have tracks
        if self._tracks:
            self._even_split()

        self.status.configure(text=f"Loaded: {self._audio_path.name}")

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
            try:
                content = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = p.read_text(encoding="latin-1")
        except OSError as e:
            messagebox.showerror("Show Splitter", f"Cannot read {p.name}:\n{e}")
            return

        show = parse_etree_file(content)
        self._show = show
        self._populate_fields(show)
        self._tracks_from_show(show)
        self.status.configure(text=f"Setlist loaded: {p.name}  ({len(self._tracks)} tracks)")

    def _populate_fields(self, show: EtreeShow) -> None:
        self._field_vars["ARTIST"].set(show.artist)
        self._field_vars["DATE"].set(show.date)
        self._field_vars["VENUE"].set(show.venue)
        self._field_vars["LOCATION"].set(show.location)
        self._source_widget.delete("1.0", "end")
        self._source_widget.insert("1.0", show.source)

    def _tracks_from_show(self, show: EtreeShow) -> None:
        n_sets = len(show.sets)
        new_tracks: list[SplitTrack] = []
        for set_i, s in enumerate(show.sets, 1):
            for t in s.tracks:
                new_tracks.append(SplitTrack(
                    number=t.global_index,
                    title=t.title,
                    start_sec=0.0,
                    disc=set_i,
                    disc_total=n_sets,
                ))
        self._tracks = new_tracks
        self._even_split()
        self._refresh_tv()
        self._update_split_btn()

    # ── Split-point helpers ───────────────────────────────────────────────────

    def _even_split(self) -> None:
        """Divide total duration evenly among tracks."""
        n = len(self._tracks)
        if n == 0 or self._duration <= 0:
            return
        step = self._duration / n
        for i, t in enumerate(self._tracks):
            t.start_sec = round(i * step, 2)
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
        self.status.configure(text="Detecting silences… (may take a minute for long files)")

        n_tracks = len(self._tracks)

        def _worker():
            ends = _detect_silences(ffmpeg, self._audio_path)
            self.after(0, lambda: self._on_silences(ends, n_tracks))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_silences(self, ends: list[float], n_tracks: int) -> None:
        self.btn_silence.configure(state="normal")
        if not ends:
            messagebox.showinfo("Show Splitter",
                                "No silences detected.\n\n"
                                "Try lowering the noise threshold in Preferences,\n"
                                "or adjust start times manually.")
            self.status.configure(text="Silence detection complete — no silences found.")
            return

        self.status.configure(
            text=f"Found {len(ends)} silence boundary(ies). "
                 f"{'Applying to track list.' if n_tracks and len(ends) >= n_tracks - 1 else 'Review below.'}")

        if not self._tracks:
            # Build tracks from detected boundaries (plus track 1 at 0:00)
            boundaries = [0.0] + ends
            self._tracks = [
                SplitTrack(number=i + 1, title=f"Track {i + 1:02d}",
                            start_sec=round(t, 2))
                for i, t in enumerate(boundaries)
            ]
            self._refresh_tv()
            self._update_split_btn()
            return

        # Have tracks — try to map the best N-1 silence boundaries to track 2..N
        self._apply_silence_boundaries(ends)

    def _apply_silence_boundaries(self, ends: list[float]) -> None:
        """Map silence_end timestamps to track start times.

        Strategy: keep track 1 at 0:00, then for each subsequent track pick
        the closest silence_end that hasn't already been used and is at least
        30 seconds past the previous track's start.
        """
        n = len(self._tracks)
        if n < 2 or not ends:
            return

        used: set[int] = set()
        self._tracks[0].start_sec = 0.0
        min_gap = 30.0

        for i in range(1, n):
            prev_start = self._tracks[i - 1].start_sec
            best_j = None
            best_dist = float("inf")
            # Rough expected position based on even split
            expected = self._duration * i / n
            for j, t in enumerate(ends):
                if j in used:
                    continue
                if t < prev_start + min_gap:
                    continue
                dist = abs(t - expected)
                if dist < best_dist:
                    best_dist = dist
                    best_j = j
            if best_j is not None:
                self._tracks[i].start_sec = round(ends[best_j], 2)
                used.add(best_j)
            # If we didn't find a good boundary, leave the current time or use even

        self._refresh_tv()

    # ── Track list UI ─────────────────────────────────────────────────────────

    def _refresh_tv(self) -> None:
        sel = self.tv.selection()
        sel_idx = self._iid_to_index(sel[0]) if sel else None

        for iid in self.tv.get_children():
            self.tv.delete(iid)

        for t in self._tracks:
            self.tv.insert("", "end", values=(
                t.number,
                _fmt_time(t.start_sec),
                t.title,
                t.disc,
            ))

        # Restore selection
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
                                 f"Invalid time: '{self.var_start.get()}'\n"
                                 "Use format  m:ss  or  h:mm:ss")
            return
        t.title = self.var_title_edit.get().strip() or t.title
        try:
            t.disc = max(1, int(self.var_disc_edit.get()))
        except ValueError:
            pass
        self._refresh_tv()

    def _add_track(self) -> None:
        n = len(self._tracks)
        new = SplitTrack(
            number=n + 1,
            title=f"Track {n + 1:02d}",
            start_sec=self._duration * n / (n + 1) if self._duration and n else 0.0,
            disc=1,
            disc_total=max((t.disc_total for t in self._tracks), default=1),
        )
        self._tracks.append(new)
        self._renumber()
        self._refresh_tv()
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
        children = self.tv.get_children()
        if idx + 1 < len(children):
            self.tv.selection_set(children[idx + 1])

    def _renumber(self) -> None:
        for i, t in enumerate(self._tracks, 1):
            t.number = i

    def _iid_to_index(self, iid: str) -> Optional[int]:
        children = self.tv.get_children()
        try:
            return list(children).index(iid)
        except ValueError:
            return None

    # ── Output dir ────────────────────────────────────────────────────────────

    def _browse_outdir(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select output folder",
            initialdir=self.var_outdir.get() or self.config_obj.get("last_output_dir") or None,
        )
        if d:
            self.var_outdir.set(d)

    # ── Split & Tag ───────────────────────────────────────────────────────────

    def _update_split_btn(self) -> None:
        ok = bool(self._audio_path and self._tracks and self.var_outdir.get())
        self.btn_split.configure(state="normal" if ok else "disabled")

    def _split_and_tag(self) -> None:
        if not self._audio_path or not self._tracks:
            return
        outdir = Path(self.var_outdir.get())
        if not outdir.exists():
            try:
                outdir.mkdir(parents=True)
            except OSError as e:
                messagebox.showerror("Show Splitter", f"Cannot create output folder:\n{e}")
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
        fmt = self.var_fmt.get()
        tracks = list(self._tracks)
        duration = self._duration

        threading.Thread(
            target=self._worker,
            args=(ffmpeg, tracks, fields, fmt, outdir, duration),
            daemon=True,
        ).start()

    def _worker(self, ffmpeg: Path, tracks: list[SplitTrack],
                fields: dict, fmt: str, outdir: Path, total_dur: float) -> None:
        ok = 0
        errors: list[str] = []
        n = len(tracks)
        ext = _build_output_ext(fmt)

        # Build album name
        album_parts: list[str] = []
        if fields.get("DATE"):
            album_parts.append(fields["DATE"])
        place = ", ".join(v for v in [fields.get("VENUE"), fields.get("LOCATION")] if v)
        if place:
            album_parts.append(place)
        # Detect source label from the source field
        src_info = detect_source(fields.get("SOURCE", ""))
        src_label = src_info.label()
        album = " ".join(album_parts)
        if src_label:
            album = f"{album} {src_label}".strip()

        disc_totals: dict[int, int] = {}
        for t in tracks:
            disc_totals[t.disc] = disc_totals.get(t.disc, 0) + 1

        for i, track in enumerate(tracks):
            fname = f"{track.number:02d} - {_sanitize_filename(track.title)}{ext}"
            out_path = outdir / fname

            start = track.start_sec
            end = tracks[i + 1].start_sec if i + 1 < n else None
            if end is not None and end <= start:
                end = None  # safety

            self.after(0, lambda lbl=f"[{i+1}/{n}] {fname}":
                       self.status.configure(text=lbl))
            try:
                _split_one(ffmpeg, self._audio_path, out_path, start, end, fmt)
            except Exception as e:
                errors.append(f"{fname}: {e}")
                self.after(0, lambda v=i+1: self.progress.configure(value=v))
                continue

            # Tag
            track_total_on_disc = disc_totals.get(track.disc, n)
            tag_dict: dict[str, str] = {
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
            if track.disc_total and track.disc_total > 1:
                tag_dict["DISCNUMBER"] = str(track.disc)
                tag_dict["DISCTOTAL"]  = str(track.disc_total)
                tag_dict["TOTALDISCS"] = str(track.disc_total)
                tag_dict["TRACKTOTAL"] = str(track_total_on_disc)
                tag_dict["TOTALTRACKS"] = str(track_total_on_disc)

            tag_dict = {k: v for k, v in tag_dict.items() if v}

            try:
                if mutagen_available():
                    write_tags(out_path, tag_dict)
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
                f"Split {ok} track(s). {len(errors)} failed:\n\n" + "\n".join(errors[:10]),
            )
        else:
            messagebox.showinfo("Show Splitter", f"Split and tagged {ok} track(s) successfully.")
        self.status.configure(text=f"Done. {ok} tracks written, {len(errors)} failed.")
        self.progress.configure(value=0)
