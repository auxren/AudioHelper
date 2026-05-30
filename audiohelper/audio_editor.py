"""Universal audio editor / track splitter.

Accepts any ffmpeg-readable audio (WAV, FLAC, MP3, DSD/DFF/DSF, AAC,
OGG, WV, APE, …).  Replaces both the old DSD-only editor and the Split
dialog.

Waveform
--------
Decoded at a fixed 4 000 Hz overview rate via ffmpeg pipe.  Streamed in
chunks so the waveform appears progressively — a thin progress bar along
the bottom edge shows decode progress.  After decode the buffer is
subsampled to at most _OVERVIEW_MAX samples using peak-preserving
bucketing, keeping memory and render time constant regardless of file
length.  Drawn as a single filled polygon on a tk.Canvas.

A canvas-size guard retries the draw until the widget is fully realized,
fixing the "waveform never appears" bug on first open.

Playback
--------
Uses ffplay (lives next to ffmpeg.exe).  Play/Pause/Stop/Seek are
implemented by launching / killing ffplay from the current position.
A 100 ms polling timer advances the playhead and updates the time display.

Split markers (CD Wave style)
-----------------------------
Left-click  → seek playhead to that position (does NOT place a marker)
Right-click → remove nearest marker within 12 px
Split button / keyboard S → place split marker at current playhead
Right-click anywhere on waveform removes nearest marker within 12 px

Track-out exports each inter-marker segment as a numbered file.
Load CUE / Save CUE sync markers with a CD-standard cue sheet.
"""

import array
import bisect
import json
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from .tools import get_tool
from .split import parse_cue, parse_cue_file_ref, parse_time_str, sec_to_hms

# ── display constants ─────────────────────────────────────────────────────
_CANVAS_H       = 260   # minimum height hint; canvas grows with the window
_MARKER_COLOR   = "#e05050"
_PLAYHEAD_COLOR = "#50c8ff"
_WAVEFORM_COLOR = "#4ea84e"
_WAVEFORM_BG    = "#101c10"
_OVERVIEW_RATE  = 4000        # Hz for decode overview
_OVERVIEW_MAX   = 16000       # max samples kept in memory
_PROGRESS_CHUNK = _OVERVIEW_RATE * 2 * 4   # bytes per progressive update (4 s of audio)

# Per-track region fill colors (dark tints, cycling palette)
_TRACK_BG = [
    "#121e14",  # green
    "#141424",  # violet
    "#241a10",  # amber
    "#10181e",  # teal-blue
    "#1e1020",  # magenta
    "#1c1e10",  # olive
    "#101e1e",  # cyan
    "#201016",  # rose
]
# Matching bright label colors (text on top of the above backgrounds)
_TRACK_FG = [
    "#60cc60",  # green
    "#8888e8",  # violet
    "#d4944e",  # amber
    "#50b8c8",  # teal-blue
    "#cc60d0",  # magenta
    "#b0cc50",  # olive
    "#50c8c8",  # cyan
    "#d05888",  # rose
]

_DSD_EXTS   = {".dff", ".dsf"}
_FMT_NORMAL = ["FLAC", "WAV", "MP3"]
_FMT_DSD    = ["FLAC", "WAV", "MP3", "DFF (copy)"]

_CUE_AUDIO_EXTS = (".wav", ".bwf", ".flac", ".mp3", ".ape", ".shn",
                    ".aiff", ".aif", ".dff", ".dsf", ".wv",
                    ".wma", ".m4a", ".ogg", ".opus", ".aac")


def _resolve_cue_audio(cue_path: str, file_ref: Optional[str]) -> Optional[Path]:
    """Locate the audio file referenced by a CUE sheet FILE directive."""
    cue     = Path(cue_path)
    cue_dir = cue.parent
    candidates: list[Path] = []

    if file_ref:
        fp = Path(file_ref)
        if fp.is_absolute():
            candidates.append(fp)
        candidates.append(cue_dir / file_ref)
        stem = fp.stem
        for ext in _CUE_AUDIO_EXTS:
            candidates.append(cue_dir / (stem + ext))

    # Fallback: audio file with same stem as the CUE sheet
    for ext in _CUE_AUDIO_EXTS:
        candidates.append(cue_dir / (cue.stem + ext))

    for c in candidates:
        if c.is_file():
            return c
    return None


# ── tooltip helper ────────────────────────────────────────────────────────

class _Tooltip:
    """Lightweight hover tooltip for any tkinter widget."""

    def __init__(self, widget, text: str) -> None:
        self._w    = widget
        self._text = text
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>",   self._show, add="+")
        widget.bind("<Leave>",   self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _show(self, _evt=None) -> None:
        if self._tip:
            return
        try:
            x = self._w.winfo_rootx()
            y = self._w.winfo_rooty() + self._w.winfo_height() + 3
        except tk.TclError:
            return
        self._tip = tk.Toplevel(self._w)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip, text=self._text,
            background="#ffffe0", foreground="#000000",
            relief="solid", borderwidth=1,
            font=("Segoe UI", 8), padx=5, pady=2,
        ).pack()

    def _hide(self, _evt=None) -> None:
        if self._tip:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


# ── helpers ───────────────────────────────────────────────────────────────

def _ffplay_path(config) -> Path:
    ffmpeg = get_tool("ffmpeg").path(config)
    candidate = ffmpeg.parent / "ffplay.exe"
    if candidate.exists():
        return candidate
    import shutil
    found = shutil.which("ffplay")
    return Path(found) if found else candidate


# ── dialog ────────────────────────────────────────────────────────────────

class AudioEditorDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Audio Editor / Track Out")
        self.config_obj  = config
        self.runner      = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("1120x840")
        self.minsize(900, 660)
        self.protocol("WM_DELETE_WINDOW", self._close)

        # waveform state
        self._waveform: Optional[array.array] = None
        self._waveform_peak: int   = 1
        self._waveform_load_pct: float = 0.0   # 0..1 loading progress; 1 = done
        self._load_token: int      = 0          # incremented each new load
        self._duration: float      = 0.0
        self._zoom: float          = 1.0
        self._view_start: float    = 0.0
        self._markers: list[float] = []
        # _track_names[i] is the name of the i-th track (0 = first region,
        # before marker[0]).  Length is always == len(_markers) + 1 whenever
        # markers exist; empty when no markers have been placed.
        self._track_names:  list[str] = []
        # _track_colors[i] overrides the palette for track i; "" = use palette
        self._track_colors: list[str] = []

        # deferred CUE tracks (set when a CUE load triggers a new audio load)
        self._pending_cue_tracks: Optional[list] = None

        # playback state
        self._ffplay:  Optional[subprocess.Popen] = None
        self._playing: bool  = False
        self._playpos: float = 0.0
        self._play_t0: float = 0.0
        self._play_p0: float = 0.0

        self._build_ui()

        if initial_files:
            for f in initial_files:
                if Path(f).is_file():
                    self.var_src.set(f)
                    self._on_load()
                    break

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        # ── top toolbar ──────────────────────────────────────────────
        tbar = ttk.Frame(self, padding=(4, 4, 4, 0))
        tbar.pack(fill="x")
        for lbl, cmd, tip in [
            ("New",       self._new,             "Clear all markers and start a new session"),
            ("Open…",     self._pick_src,         "Open an audio file"),
            ("Load CUE…", self._load_cue_dialog,  "Import split markers from a CUE sheet"),
            ("Save CUE…", self._save_cue_dialog,  "Export split markers as a CUE sheet"),
        ]:
            btn = ttk.Button(tbar, text=lbl, command=cmd)
            btn.pack(side="left", padx=2)
            _Tooltip(btn, tip)

        # ── source / info ─────────────────────────────────────────────
        sf = ttk.LabelFrame(self, text="Source file", padding=6)
        sf.pack(fill="x", padx=8, pady=(4, 2))
        self.var_src = tk.StringVar()
        ttk.Entry(sf, textvariable=self.var_src).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        btn_browse = ttk.Button(sf, text="…", width=3, command=self._pick_src)
        btn_browse.grid(row=0, column=1)
        _Tooltip(btn_browse, "Browse for audio file")
        btn_load = ttk.Button(sf, text="Load", command=self._on_load)
        btn_load.grid(row=0, column=2, padx=(6, 0))
        _Tooltip(btn_load, "Load file and decode waveform overview")
        sf.columnconfigure(0, weight=1)
        self.lbl_info = ttk.Label(sf, text="(no file loaded)",
                                  foreground="gray", font=("Consolas", 8))
        self.lbl_info.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # ── waveform ──────────────────────────────────────────────────
        wf = ttk.LabelFrame(self, text="Waveform", padding=4)
        wf.pack(fill="both", expand=True, padx=8, pady=4)

        tb = ttk.Frame(wf)
        tb.pack(fill="x")

        # Zoom controls
        btn_zm = ttk.Button(tb, text="−", width=2, command=self._zoom_out)
        btn_zm.pack(side="left")
        _Tooltip(btn_zm, "Zoom out  (Shift+scroll)")

        btn_zp = ttk.Button(tb, text="+", width=2, command=self._zoom_in)
        btn_zp.pack(side="left", padx=2)
        _Tooltip(btn_zp, "Zoom in  (Shift+scroll)")

        btn_fit = ttk.Button(tb, text="Fit", command=self._zoom_fit)
        btn_fit.pack(side="left")
        _Tooltip(btn_fit, "Fit entire file in view")

        self.lbl_zoom = ttk.Label(tb, text="1×", foreground="gray")
        self.lbl_zoom.pack(side="left", padx=8)

        # Split button — CD Wave style: places marker at current playhead
        btn_split = ttk.Button(tb, text="Split", command=self._split_at_cursor)
        btn_split.pack(side="left", padx=(12, 2))
        _Tooltip(btn_split, "Place split marker at cursor position  (S)")

        # Right-side controls
        self.lbl_mc = ttk.Label(tb, text="0 markers", foreground="gray")
        self.lbl_mc.pack(side="right")
        btn_cm = ttk.Button(tb, text="Clear markers", command=self._clear_markers)
        btn_cm.pack(side="right", padx=4)
        _Tooltip(btn_cm, "Remove all split markers")

        cvw = ttk.Frame(wf)
        cvw.pack(fill="both", expand=True, pady=(4, 0))
        self.canvas = tk.Canvas(cvw, height=_CANVAS_H, bg=_WAVEFORM_BG,
                                cursor="crosshair", highlightthickness=0)
        self.hbar = ttk.Scrollbar(cvw, orient="horizontal",
                                  command=self._on_hscroll)
        self.hbar.pack(side="bottom", fill="x")
        self.canvas.pack(fill="both", expand=True)

        # ── track list ────────────────────────────────────────────────
        ttk.Separator(wf, orient="horizontal").pack(fill="x", pady=(4, 0))
        trk_frame = ttk.Frame(wf)
        trk_frame.pack(fill="x", pady=(0, 2))

        cols = ("num", "name", "start", "dur", "clr")
        self.track_tree = ttk.Treeview(
            trk_frame, columns=cols, show="headings",
            height=5, selectmode="browse",
        )
        self.track_tree.heading("num",   text="#",           anchor="center")
        self.track_tree.heading("name",  text="Track name",  anchor="w")
        self.track_tree.heading("start", text="Start",       anchor="center")
        self.track_tree.heading("dur",   text="Duration",    anchor="center")
        self.track_tree.heading("clr",   text="",            anchor="center")
        self.track_tree.column("num",   width=30,  minwidth=30,  stretch=False, anchor="center")
        self.track_tree.column("name",  width=300, minwidth=120, stretch=True,  anchor="w")
        self.track_tree.column("start", width=90,  minwidth=80,  stretch=False, anchor="center")
        self.track_tree.column("dur",   width=90,  minwidth=80,  stretch=False, anchor="center")
        self.track_tree.column("clr",   width=22,  minwidth=22,  stretch=False, anchor="center")

        for i in range(len(_TRACK_FG)):
            self.track_tree.tag_configure(
                f"trk{i}",
                foreground=_TRACK_FG[i],
                background=_TRACK_BG[i],
            )

        trk_sb = ttk.Scrollbar(trk_frame, orient="vertical",
                               command=self.track_tree.yview)
        self.track_tree.configure(yscrollcommand=trk_sb.set)
        self.track_tree.pack(side="left", fill="both", expand=True)
        trk_sb.pack(side="right", fill="y")

        self.track_tree.bind("<ButtonRelease-1>", self._on_track_click)
        self.track_tree.bind("<Double-1>",        self._on_track_dblclick)

        # Left-click = seek cursor  |  right-click = remove nearest marker
        self.canvas.bind("<Configure>",  lambda _: self._redraw())
        self.canvas.bind("<Button-1>",   self._seek_click)
        self.canvas.bind("<Button-3>",   self._remove_marker)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>",   self._wheel)
        self.canvas.bind("<Button-5>",   self._wheel)

        # ── playback console ─────────────────────────────────────────
        pb = ttk.LabelFrame(self, text="Playback", padding=4)
        pb.pack(fill="x", padx=8, pady=(0, 2))

        self.lbl_pos = ttk.Label(pb, text="00:00:00.000",
                                 font=("Consolas", 11), foreground=_PLAYHEAD_COLOR)
        self.lbl_pos.pack(side="left", padx=(0, 4))
        # CD-frame counter (75 fps, matches CUE sheet format)
        self.lbl_frame = ttk.Label(pb, text="f:00",
                                   font=("Consolas", 9), foreground="#6a9a6a")
        self.lbl_frame.pack(side="left", padx=(0, 10))
        _Tooltip(self.lbl_frame, "CD frame within current second (75 fps — matches CUE sheet format)")

        # (symbol, command, tooltip, (bg, fg, active-bg))
        _C_PLAY  = ("#1a6820", "#80e880", "#22a030")
        _C_STOP  = ("#6a1818", "#ffaaaa", "#922828")
        _C_PAUSE = ("#685200", "#ffd060", "#927400")
        _C_NAV   = ("#182858", "#80b8ff", "#284888")
        _C_DEF   = ("#242424", "#c0c0c0", "#343434")
        for sym, cmd, tip, clr in [
            ("⏮",  self._pb_begin,       "Go to beginning",                    _C_DEF),
            ("◀◀", self._pb_back,        "Back 10 seconds",                    _C_DEF),
            ("|◀", self._pb_prev_marker, "Jump to previous split marker",      _C_NAV),
            ("■",  self._pb_stop,        "Stop",                               _C_STOP),
            ("▶",  self._pb_play,        "Play from cursor position  (Space)", _C_PLAY),
            ("⏸",  self._pb_pause,       "Pause  (Space)",                     _C_PAUSE),
            ("▶|", self._pb_next_marker, "Jump to next split marker",          _C_NAV),
            ("▶▶", self._pb_fwd,         "Forward 10 seconds",                 _C_DEF),
            ("⏭",  self._pb_end,         "Go to end",                          _C_DEF),
        ]:
            bg, fg, abg = clr
            btn = tk.Button(pb, text=sym, width=3, command=cmd,
                            bg=bg, fg=fg,
                            activebackground=abg, activeforeground=fg,
                            relief="flat", font=("Segoe UI", 10),
                            cursor="hand2", borderwidth=1)
            btn.pack(side="left", padx=1)
            _Tooltip(btn, tip)

        ttk.Label(pb, text="Vol:").pack(side="left", padx=(10, 0))
        self.var_vol = tk.IntVar(value=100)
        vol_slider = ttk.Scale(pb, variable=self.var_vol, from_=0, to=150,
                               orient="horizontal", length=80)
        vol_slider.pack(side="left")
        _Tooltip(vol_slider, "Playback volume (0–150%)")

        # ── trim / gain ───────────────────────────────────────────────
        mid = ttk.Frame(self)
        mid.pack(fill="x", padx=8, pady=2)
        trim = ttk.LabelFrame(mid,
                              text="Trim for 'Export selection' (HH:MM:SS.mmm)",
                              padding=6)
        trim.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Label(trim, text="Start:").grid(row=0, column=0, sticky="w")
        self.var_start = tk.StringVar()
        ttk.Entry(trim, textvariable=self.var_start, width=14).grid(
            row=0, column=1, padx=4, sticky="w")
        ttk.Label(trim, text="End:").grid(
            row=0, column=2, sticky="w", padx=(8, 0))
        self.var_end = tk.StringVar()
        ttk.Entry(trim, textvariable=self.var_end, width=14).grid(
            row=0, column=3, padx=4, sticky="w")
        gf = ttk.LabelFrame(mid, text="Gain (dB)", padding=6)
        gf.pack(side="left")
        self.var_gain = tk.DoubleVar(value=0.0)
        ttk.Spinbox(gf, from_=-24.0, to=24.0, increment=0.5,
                    textvariable=self.var_gain, width=7, format="%.1f").pack()

        # ── output options ────────────────────────────────────────────
        of = ttk.LabelFrame(self, text="Output", padding=6)
        of.pack(fill="x", padx=8, pady=4)
        ttk.Label(of, text="Format:").grid(row=0, column=0, sticky="w")
        self.var_fmt = tk.StringVar(value="FLAC")
        self.fmt_cb = ttk.Combobox(of, textvariable=self.var_fmt,
                                   state="readonly", width=12,
                                   values=_FMT_NORMAL)
        self.fmt_cb.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(of, text="Rate:").grid(
            row=0, column=2, sticky="w", padx=(10, 0))
        self.var_sr = tk.StringVar(value="Auto")
        ttk.Combobox(of, textvariable=self.var_sr, state="readonly", width=9,
                     values=["Auto", "44100", "48000", "88200",
                             "96000", "176400", "192000"]).grid(
                                 row=0, column=3, sticky="w", padx=4)
        ttk.Label(of, text="Bits:").grid(
            row=0, column=4, sticky="w", padx=(10, 0))
        self.var_bits = tk.StringVar(value="Auto")
        ttk.Combobox(of, textvariable=self.var_bits, state="readonly",
                     width=6, values=["Auto", "16", "24", "32"]).grid(
                         row=0, column=5, sticky="w", padx=4)
        ttk.Label(of, text="Output folder:").grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        self.var_outdir = tk.StringVar()
        ttk.Entry(of, textvariable=self.var_outdir).grid(
            row=1, column=1, columnspan=4, sticky="ew", padx=4, pady=(4, 0))
        btn_outbrowse = ttk.Button(of, text="…", width=3, command=self._pick_outdir)
        btn_outbrowse.grid(row=1, column=5, pady=(4, 0))
        _Tooltip(btn_outbrowse, "Browse for output folder")
        of.columnconfigure(1, weight=1)

        # ── button bar ────────────────────────────────────────────────
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        btn_cancel = ttk.Button(bar, text="Cancel", command=self._close)
        btn_cancel.pack(side="right")
        _Tooltip(btn_cancel, "Close this dialog")
        btn_exp = ttk.Button(bar, text="Export selection",
                             command=self._export_selection)
        btn_exp.pack(side="right", padx=4)
        _Tooltip(btn_exp, "Export the trimmed Start→End region as a single file")
        btn_to = ttk.Button(bar, text="Track out", command=self._track_out)
        btn_to.pack(side="right", padx=4)
        _Tooltip(btn_to,
                 "Export each region between split markers as a numbered file")
        self.lbl_status = ttk.Label(bar, text="", foreground="gray", anchor="w")
        self.lbl_status.pack(side="left", fill="x", expand=True)

        # Keyboard shortcuts (fire when dialog has focus, not captured by entries)
        self.bind("<Key-s>",      lambda _e: self._split_at_cursor())
        self.bind("<Key-S>",      lambda _e: self._split_at_cursor())
        self.bind("<KeyPress-space>", self._pb_toggle)

        # Arrow-key cursor navigation
        for seq in ("<Left>", "<Right>", "<Up>", "<Down>",
                    "<Shift-Left>", "<Shift-Right>",
                    "<Shift-Up>", "<Shift-Down>"):
            self.bind(seq, self._key_step)

    # ------------------------------------------------------------------ #
    # File operations
    # ------------------------------------------------------------------ #

    def _new(self) -> None:
        self._pb_stop()
        self._load_token += 1
        self._waveform = None
        self._waveform_peak = 1
        self._waveform_load_pct = 0.0
        self._duration = 0.0
        self._zoom = 1.0
        self._view_start = 0.0
        self._markers.clear()
        self._track_names.clear()
        self._track_colors.clear()
        self._pending_cue_tracks = None
        self.var_src.set("")
        self.var_start.set("")
        self.var_end.set("")
        self.lbl_info.configure(text="(no file loaded)", foreground="gray")
        self._redraw()
        self._update_mc()   # also calls _update_track_list
        self._set_status("New project")

    def _pick_src(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Open audio file",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio", ("*.wav", "*.bwf", "*.flac", "*.mp3", "*.aac",
                           "*.m4a", "*.ogg", "*.opus", "*.aiff", "*.aif",
                           "*.dff", "*.dsf", "*.wv", "*.ape", "*.wma")),
                ("All files", "*.*"),
            ],
        )
        if f:
            self.var_src.set(f)
            self.config_obj["last_input_dir"] = str(Path(f).parent)
            self.config_obj.save()
            self._on_load()

    def _on_load(self) -> None:
        src = self.var_src.get().strip()
        if not src or not Path(src).is_file():
            return
        self._pb_stop()
        self._load_token += 1
        self._waveform = None
        self._waveform_peak = 1
        self._waveform_load_pct = 0.0
        self._markers.clear()
        self._track_names.clear()
        self._track_colors.clear()
        self._zoom = 1.0
        self._view_start = 0.0
        self._duration = 0.0
        self._playpos  = 0.0
        self.var_start.set("")
        self.var_end.set("")
        self.lbl_info.configure(text="Loading…", foreground="gray")
        self._set_status("Probing file…")
        self._redraw()
        self._update_mc()

        ext = Path(src).suffix.lower()
        self.fmt_cb.configure(
            values=_FMT_DSD if ext in _DSD_EXTS else _FMT_NORMAL)
        if self.var_fmt.get() not in self.fmt_cb["values"]:
            self.var_fmt.set("FLAC")

        token = self._load_token
        threading.Thread(target=self._load_thread,
                         args=(src, token), daemon=True).start()

    # ------------------------------------------------------------------ #
    # Background decode (progressive streaming)
    # ------------------------------------------------------------------ #

    def _load_thread(self, src: str, token: int) -> None:
        ffprobe = get_tool("ffprobe").path(self.config_obj)
        ffmpeg  = get_tool("ffmpeg").path(self.config_obj)

        info, duration = self._do_probe(src, ffprobe)
        if self._load_token != token:
            return
        self._duration = duration
        self.after(0, lambda: self._apply_probe_ui(info, duration, src))

        if not ffmpeg.exists():
            self.after(0, lambda: self._set_status(
                "ffmpeg not found — Tools → Update all CLI tools"))
            return

        self.after(0, lambda: self._set_status("Decoding waveform…  0%"))

        try:
            proc = subprocess.Popen(
                [str(ffmpeg), "-v", "error",
                 "-i", src,
                 "-ac", "1",
                 "-ar", str(_OVERVIEW_RATE),
                 "-f", "s16le", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            self.after(0, lambda: self._set_status(f"Decode error: {e}"))
            return

        # Expected total bytes (mono 16-bit at overview rate × duration)
        expected = max(1, int(duration * _OVERVIEW_RATE * 2)) if duration > 0 else 1

        raw = bytearray()
        last_post = 0   # byte count at last progressive update

        while True:
            if self._load_token != token:
                proc.kill()
                proc.wait()
                return
            chunk = proc.stdout.read(_PROGRESS_CHUNK)
            if not chunk:
                break
            raw.extend(chunk)
            # Post update every _PROGRESS_CHUNK bytes
            if len(raw) - last_post >= _PROGRESS_CHUNK:
                last_post = len(raw)
                self._post_waveform(bytes(raw), expected, token, final=False)

        proc.wait()

        if self._load_token != token:
            return

        if not raw:
            self.after(0, lambda: self._set_status(
                "No waveform data returned by ffmpeg"))
            return

        # Final full-resolution update
        self._post_waveform(bytes(raw), expected, token, final=True)
        self.after(0, self._on_loaded)

    def _post_waveform(self, raw: bytes, expected: int,
                       token: int, final: bool) -> None:
        """Subsample raw PCM bytes and schedule a main-thread waveform update."""
        nb = len(raw) - len(raw) % 2
        if nb < 2:
            return
        data: array.array = array.array("h")
        data.frombytes(raw[:nb])
        n = len(data)
        if n > _OVERVIEW_MAX:
            step = n / _OVERVIEW_MAX
            data = array.array("h", [
                max(data[int(i * step):min(n, int((i + 1) * step))],
                    key=abs, default=0)
                for i in range(_OVERVIEW_MAX)
            ])
        peak = max((abs(v) for v in data), default=1)
        pct  = 1.0 if final else min(0.99, len(raw) / expected)

        def apply():
            if self._load_token != token:
                return
            self._waveform      = data
            self._waveform_peak = max(peak, 1)
            self._waveform_load_pct = pct
            if not final:
                self._set_status(f"Decoding waveform…  {int(pct * 100)}%")
            self._redraw()
        self.after(0, apply)

    def _do_probe(self, src: str, ffprobe: Path) -> tuple[str, float]:
        if not ffprobe.exists():
            return "ffprobe not found", 0.0
        try:
            r = subprocess.run(
                [str(ffprobe), "-v", "error", "-show_format",
                 "-show_streams", "-of", "json", src],
                capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            j = json.loads(r.stdout or "{}")
        except Exception as e:
            return f"Probe error: {e}", 0.0

        fmt     = j.get("format") or {}
        streams = j.get("streams") or []
        audio   = next((s for s in streams
                        if s.get("codec_type") == "audio"), {})
        duration = float(fmt.get("duration") or audio.get("duration") or 0)
        sr       = int(audio.get("sample_rate") or 0)
        channels = int(audio.get("channels") or 0)
        codec    = (audio.get("codec_name") or "?").upper()
        size_b   = int(fmt.get("size") or 0)
        br_kbps  = int(float(fmt.get("bit_rate") or 0)) // 1000
        dur_str  = sec_to_hms(duration) if duration else "?"
        siz_str  = f"{size_b / 1_048_576:.1f} MiB" if size_b else "?"
        br_str   = f"  |  {br_kbps:,} kbps" if br_kbps else ""
        info = (f"{codec}  |  {sr:,} Hz  |  {channels} ch  |  "
                f"{dur_str}  |  {siz_str}{br_str}")
        return info, duration

    def _apply_probe_ui(self, info: str, duration: float, src: str) -> None:
        self.lbl_info.configure(text=info, foreground="gray")
        if duration:
            self.var_end.set(sec_to_hms(duration))
        if not self.var_outdir.get():
            self.var_outdir.set(str(Path(src).parent))

    def _on_loaded(self) -> None:
        self._zoom = 1.0
        self._view_start = 0.0
        self._waveform_load_pct = 1.0
        pending = self._pending_cue_tracks
        self._pending_cue_tracks = None
        if pending is not None:
            self._apply_cue_markers(pending)   # also calls _update_track_list
            n = len(pending)
            self._set_status(
                f"Ready — {n} CUE track{'s' if n != 1 else ''} loaded"
                "  │  click waveform to seek  │  right-click to remove a marker")
        else:
            self._update_track_list()
            self._set_status(
                "Ready — click waveform to seek  │  press Split (or S) to place a marker"
                "  │  right-click to remove nearest marker")
        self.update_idletasks()
        self._redraw()

    # ------------------------------------------------------------------ #
    # Waveform rendering
    # ------------------------------------------------------------------ #

    def _cw(self) -> int:
        return self.canvas.winfo_width()

    def _view_dur(self) -> float:
        dur = self._duration if self._duration > 0 else 1.0
        return dur / self._zoom

    def _redraw(self) -> None:
        cw = self._cw()
        if cw <= 1:
            if self._waveform is not None:
                self.after(80, self._redraw)
            return

        self.canvas.delete("all")
        ch  = max(_CANVAS_H, self.canvas.winfo_height())
        mid = ch // 2

        if self._waveform is None:
            self.canvas.create_line(0, mid, cw, mid, fill="#203020", width=1)
            self._sync_scrollbar(cw)
            return

        data = self._waveform
        n    = len(data)
        dur  = self._duration if self._duration > 0 else 1.0
        sc   = (mid - 4) / self._waveform_peak

        vd  = self._view_dur()
        t0  = max(0.0, min(self._view_start, dur - vd))
        self._view_start = t0
        t1  = min(t0 + vd, dur)

        s0  = max(0, int(t0 / dur * n))
        s1  = min(n, max(s0 + 1, int(t1 / dur * n) + 1))
        vis = max(s1 - s0, 1)

        self.canvas.create_line(0, mid, cw, mid, fill="#203020", width=1)

        # ── per-track colored waveform polygons ───────────────────────
        # Each region between split markers gets its own waveform color.
        # When no markers exist, the whole waveform uses _WAVEFORM_COLOR.
        n_tracks = max(1, len(self._markers) + 1)
        track_tops: list[list[int]] = [[] for _ in range(n_tracks)]
        track_bots: list[list[int]] = [[] for _ in range(n_tracks)]

        mptr = 0   # advances left-to-right through sorted markers
        for px in range(cw):
            i0 = s0 + int(px / cw * vis)
            i1 = s0 + int((px + 1) / cw * vis)
            if i1 <= i0:
                i1 = i0 + 1
            i1 = min(i1, n)
            if i0 >= n:
                break
            t_px = t0 + px / cw * vd
            while mptr < len(self._markers) and self._markers[mptr] <= t_px:
                mptr += 1
            tidx = min(mptr, n_tracks - 1)
            chunk = data[i0:i1]
            if not chunk:
                track_tops[tidx].extend([px, mid])
                track_bots[tidx].extend([px, mid])
                continue
            mn, mx = min(chunk), max(chunk)
            track_tops[tidx].extend([px, max(2, min(ch - 6, int(mid - mx * sc)))])
            track_bots[tidx].extend([px, max(2, min(ch - 6, int(mid - mn * sc)))])

        for i in range(n_tracks):
            top, bot = track_tops[i], track_bots[i]
            if len(top) < 4:
                continue
            color = (self._track_color(i)
                     if self._markers else _WAVEFORM_COLOR)
            pairs = [(bot[k], bot[k + 1]) for k in range(0, len(bot), 2)]
            bot_rev = [c for pair in reversed(pairs) for c in pair]
            self.canvas.create_polygon(top + bot_rev, fill=color, outline="")

        # ── track name labels (drawn over the waveform) ───────────────
        if self._markers and self._duration > 0:
            bounds = [0.0] + self._markers + [self._duration]
            for i in range(len(bounds) - 1):
                ts, te = bounds[i], bounds[i + 1]
                rx0 = self._t_to_px(ts, cw, vd, t0)
                rx1 = self._t_to_px(te, cw, vd, t0)
                rx0 = max(0, rx0)
                rx1 = min(cw, rx1)
                if rx1 - rx0 < 10:
                    continue
                name = (self._track_names[i]
                        if i < len(self._track_names) and self._track_names[i]
                        else f"Track {i + 1:02d}")
                fg  = self._track_color(i)
                tag = f"lbl_{i}"
                self.canvas.create_text(
                    rx0 + 4, 6, text=name, anchor="nw",
                    fill=fg, font=("Consolas", 8, "bold"),
                    tags=(tag, "track_label"),
                )

        # Split marker lines
        for m in self._markers:
            px = self._t_to_px(m, cw, vd, t0)
            if 0 <= px < cw:
                self.canvas.create_line(px, 0, px, ch - 5,
                                        fill=_MARKER_COLOR, width=2)

        # Playhead
        if self._duration > 0:
            ppx = self._t_to_px(self._playpos, cw, vd, t0)
            if 0 <= ppx < cw:
                self.canvas.create_line(ppx, 0, ppx, ch - 5,
                                        fill=_PLAYHEAD_COLOR, width=1,
                                        dash=(4, 3))

        # Loading progress bar — 5 px strip along the bottom
        pct = self._waveform_load_pct
        if 0.0 < pct < 1.0:
            loaded_px = max(1, int(cw * pct))
            self.canvas.create_rectangle(0, ch - 5, loaded_px, ch,
                                         fill="#2d7a2d", outline="")
            self.canvas.create_rectangle(loaded_px, ch - 5, cw, ch,
                                         fill="#162016", outline="")
        elif pct >= 1.0:
            # Briefly keep a full green bar; it disappears on next _redraw after done
            pass

        self._sync_scrollbar(cw)

    def _sync_scrollbar(self, cw: int) -> None:
        dur = self._duration
        if dur <= 0:
            self.hbar.set(0.0, 1.0)
            return
        vd = self._view_dur()
        t0 = self._view_start
        lo = max(0.0, min(1.0, t0 / dur))
        hi = max(lo,  min(1.0, (t0 + vd) / dur))
        self.hbar.set(lo, hi)

    def _t_to_px(self, t: float, cw: int, vd: float, t0: float) -> int:
        return int((t - t0) / vd * cw) if vd > 0 else 0

    def _px_to_t(self, px: int) -> float:
        cw = self._cw()
        if cw <= 1:
            return 0.0
        vd = self._view_dur()
        t  = self._view_start + px / cw * vd
        return max(0.0, min(self._duration, t))

    # ------------------------------------------------------------------ #
    # Scrollbar
    # ------------------------------------------------------------------ #

    def _on_hscroll(self, *args) -> None:
        dur = self._duration
        if dur <= 0:
            return
        vd  = self._view_dur()
        cmd = args[0]
        if cmd == "moveto":
            t0 = float(args[1]) * dur
        elif cmd == "scroll":
            step  = int(args[1])
            unit  = args[2]
            delta = (vd * 0.1 if unit == "units" else vd) * step
            t0    = self._view_start + delta
        else:
            return
        self._view_start = max(0.0, min(t0, max(0.0, dur - vd)))
        self._redraw()

    # ------------------------------------------------------------------ #
    # Zoom
    # ------------------------------------------------------------------ #

    def _zoom_in(self)  -> None: self._do_zoom(self._zoom * 2.0)
    def _zoom_out(self) -> None: self._do_zoom(self._zoom / 2.0)
    def _zoom_fit(self) -> None: self._do_zoom(1.0)

    def _do_zoom(self, z: float) -> None:
        z      = max(1.0, min(z, 512.0))
        centre = self._view_start + self._view_dur() / 2
        self._zoom = z
        vd  = self._view_dur()
        dur = self._duration if self._duration > 0 else 1.0
        self._view_start = max(0.0, min(centre - vd / 2, dur - vd))
        self.lbl_zoom.configure(text="1×" if z < 2 else f"{int(z)}×")
        self._redraw()

    def _wheel(self, event) -> None:
        up    = (event.num == 4) or (getattr(event, "delta", 0) > 0)
        shift = bool(event.state & 0x0001)
        if shift:
            # Shift+scroll → zoom (zoom keeps the hovered time position centred)
            if up:
                self._zoom_in()
            else:
                self._zoom_out()
        else:
            # Plain scroll → pan the view left / right
            if self._duration <= 0:
                return
            vd   = self._view_dur()
            step = vd * 0.15   # 15 % of the visible window per tick
            dur  = self._duration
            if up:
                self._view_start = max(0.0, self._view_start - step)
            else:
                self._view_start = min(max(0.0, dur - vd), self._view_start + step)
            self._redraw()

    # ------------------------------------------------------------------ #
    # Waveform interaction — seek & split markers (CD Wave style)
    # ------------------------------------------------------------------ #

    def _seek_click(self, event) -> None:
        """Left-click: seek OR rename track label when clicked."""
        if self._duration <= 0:
            return
        # Check whether the click landed on a track label
        hits = self.canvas.find_overlapping(
            event.x - 2, event.y - 2, event.x + 2, event.y + 2)
        for item in hits:
            tags = self.canvas.gettags(item)
            if "track_label" in tags:
                for tag in tags:
                    if tag.startswith("lbl_"):
                        self._rename_track(int(tag[4:]))
                        return
        t = self._px_to_t(event.x)
        was_playing = self._playing
        if was_playing:
            self._pb_pause()
        self._playpos = t
        self._update_pos_display(t)
        self._redraw()
        if was_playing:
            self._pb_play()

    def _split_at_cursor(self) -> None:
        """Place a split marker at the current playhead position."""
        if self._duration <= 0:
            return
        t = self._playpos
        if any(abs(m - t) < 0.05 for m in self._markers):
            self._set_status(f"Split marker already near {sec_to_hms(t)}")
            return
        idx = bisect.bisect_right(self._markers, t)
        self._markers.insert(idx, t)
        # Ensure _track_names / _track_colors cover all tracks; extend if sparse
        total_before = len(self._markers)   # after insert = n_tracks
        while len(self._track_names) < total_before:
            self._track_names.append(f"Track {len(self._track_names) + 1:02d}")
        while len(self._track_colors) < total_before:
            self._track_colors.append("")
        # Insert default name/color for the new right-hand region
        self._track_names.insert(idx + 1, f"Track {total_before + 1:02d}")
        self._track_colors.insert(idx + 1, "")
        self._redraw()
        self._update_mc()
        self._set_status(f"Split marker placed at {sec_to_hms(t)}")

    def _remove_marker(self, event) -> None:
        if not self._markers:
            return
        cw = self._cw()
        vd = self._view_dur()
        t0 = self._view_start
        best = min(range(len(self._markers)),
                   key=lambda i: abs(
                       self._t_to_px(self._markers[i], cw, vd, t0) - event.x))
        if abs(self._t_to_px(self._markers[best], cw, vd, t0) - event.x) < 12:
            self._markers.pop(best)
            # Remove the right-hand track name/color created by this marker
            if best + 1 < len(self._track_names):
                self._track_names.pop(best + 1)
            if best + 1 < len(self._track_colors):
                self._track_colors.pop(best + 1)
            self._redraw()
            self._update_mc()

    def _clear_markers(self) -> None:
        self._markers.clear()
        self._track_names.clear()
        self._track_colors.clear()
        self._redraw()
        self._update_mc()

    def _track_color(self, idx: int) -> str:
        """Return the effective color for track idx (custom override or palette)."""
        if idx < len(self._track_colors) and self._track_colors[idx]:
            return self._track_colors[idx]
        return _TRACK_FG[idx % len(_TRACK_FG)]

    def _update_mc(self) -> None:
        n = len(self._markers)
        self.lbl_mc.configure(text=f"{n} marker{'s' if n != 1 else ''}")
        self._update_track_list()

    def _rename_track(self, track_idx: int) -> None:
        """Show an inline Entry on the canvas to rename a track."""
        tag  = f"lbl_{track_idx}"
        items = self.canvas.find_withtag(tag)
        if not items:
            return
        # Get the text item's on-screen position
        coords = self.canvas.coords(items[0])
        if len(coords) < 2:
            return
        lx, ly = int(coords[0]), int(coords[1])

        # Extend _track_names to cover this index with defaults
        while len(self._track_names) <= track_idx:
            self._track_names.append(
                f"Track {len(self._track_names) + 1:02d}")

        current = self._track_names[track_idx] or f"Track {track_idx + 1:02d}"
        var = tk.StringVar(value=current)
        entry = ttk.Entry(self.canvas, textvariable=var,
                          width=16, font=("Consolas", 8))
        win_id = self.canvas.create_window(lx, ly, anchor="nw", window=entry)
        entry.select_range(0, "end")
        entry.focus_set()

        def _commit(_evt=None):
            new_name = var.get().strip()
            self._track_names[track_idx] = new_name
            try:
                self.canvas.delete(win_id)
            except tk.TclError:
                pass
            self._redraw()
            self._update_track_list()

        def _cancel(_evt=None):
            try:
                self.canvas.delete(win_id)
            except tk.TclError:
                pass
            self._redraw()

        entry.bind("<Return>",   _commit)
        entry.bind("<KP_Enter>", _commit)
        entry.bind("<Escape>",   _cancel)
        entry.bind("<FocusOut>", _commit)

    # ------------------------------------------------------------------ #
    # Track list
    # ------------------------------------------------------------------ #

    def _update_track_list(self) -> None:
        """Rebuild the track list treeview from current state."""
        for row in self.track_tree.get_children():
            self.track_tree.delete(row)
        if not self._markers or self._duration <= 0:
            return
        bounds = [0.0] + self._markers + [self._duration]
        for i in range(len(bounds) - 1):
            ts  = bounds[i]
            dur = bounds[i + 1] - ts
            name = (self._track_names[i]
                    if i < len(self._track_names) and self._track_names[i]
                    else f"Track {i + 1:02d}")
            color = self._track_color(i)
            bg    = _TRACK_BG[i % len(_TRACK_BG)]
            tag   = f"row_{i}"
            self.track_tree.tag_configure(tag, foreground=color, background=bg)
            self.track_tree.insert(
                "", "end", iid=str(i),
                values=(f"{i + 1:02d}", name, sec_to_hms(ts), sec_to_hms(dur), "■"),
                tags=(tag,),
            )

    def _on_track_click(self, event) -> None:
        """Single click: seek to track start, or open color picker on the ■ column."""
        sel = self.track_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        col = self.track_tree.identify_column(event.x)
        if col == "#5":   # "clr" is the 5th column
            self._pick_track_color(idx)
            return
        bounds = [0.0] + self._markers
        if idx >= len(bounds):
            return
        t = bounds[idx]
        was_playing = self._playing
        if was_playing:
            self._pb_pause()
        self._playpos = t
        self._update_pos_display(t)
        # Scroll view so the track start is visible
        vd = self._view_dur()
        if t < self._view_start or t > self._view_start + vd * 0.9:
            self._view_start = max(0.0, t - vd * 0.1)
        self._redraw()
        if was_playing:
            self._pb_play()

    def _on_track_dblclick(self, event) -> None:
        """Double click: rename track inline in the treeview."""
        sel = self.track_tree.selection()
        if not sel:
            return
        idx  = int(sel[0])
        bbox = self.track_tree.bbox(sel[0], column="name")
        if not bbox:
            return
        x, y, w, h = bbox

        while len(self._track_names) <= idx:
            self._track_names.append(f"Track {len(self._track_names) + 1:02d}")
        current = self._track_names[idx] or f"Track {idx + 1:02d}"

        var   = tk.StringVar(value=current)
        entry = ttk.Entry(self.track_tree, textvariable=var,
                          font=("Consolas", 9))
        entry.place(x=x, y=y, width=w, height=h)
        entry.select_range(0, "end")
        entry.focus_set()

        def _commit(_evt=None):
            new_name = var.get().strip()
            self._track_names[idx] = new_name
            try:
                entry.destroy()
            except tk.TclError:
                pass
            self._update_track_list()
            self._redraw()

        def _cancel(_evt=None):
            try:
                entry.destroy()
            except tk.TclError:
                pass

        entry.bind("<Return>",   _commit)
        entry.bind("<KP_Enter>", _commit)
        entry.bind("<Escape>",   _cancel)
        entry.bind("<FocusOut>", _commit)

    def _pick_track_color(self, idx: int) -> None:
        """Open a color chooser to override the color for track idx."""
        from tkinter import colorchooser
        name = (self._track_names[idx]
                if idx < len(self._track_names) and self._track_names[idx]
                else f"Track {idx + 1:02d}")
        result = colorchooser.askcolor(
            color=self._track_color(idx),
            parent=self,
            title=f"Color for {name}",
        )
        if result and result[1]:
            while len(self._track_colors) <= idx:
                self._track_colors.append("")
            self._track_colors[idx] = result[1]
            self._update_track_list()
            self._redraw()

    # ------------------------------------------------------------------ #
    # CUE sheet
    # ------------------------------------------------------------------ #

    def _load_cue_dialog(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Open CUE sheet",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("CUE sheets", "*.cue"), ("All files", "*.*")],
        )
        if f:
            self._load_cue_file(f)

    def _load_cue_file(self, path: str) -> None:
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            messagebox.showerror("Trader's Little Jedi", f"Cannot read CUE:\n{e}")
            return
        tracks = parse_cue(text)
        if not tracks:
            messagebox.showwarning("Trader's Little Jedi",
                                   "No AUDIO tracks with INDEX 01 found.")
            return

        file_ref   = parse_cue_file_ref(text)
        audio_path = _resolve_cue_audio(path, file_ref)
        current    = self.var_src.get().strip()

        # Case 1: resolved audio matches file already loaded → markers only
        if audio_path and current and \
                Path(current).resolve() == audio_path.resolve():
            self._apply_cue_markers(tracks)
            self._set_status(
                f"Loaded {len(tracks)} tracks from {Path(path).name}")
            return

        # Case 2: no audio found → prompt user
        if audio_path is None:
            if current and Path(current).is_file():
                # Already have audio loaded; just apply markers without asking
                self._apply_cue_markers(tracks)
                self._set_status(
                    f"Loaded {len(tracks)} tracks from {Path(path).name}")
                return
            chosen = filedialog.askopenfilename(
                parent=self,
                title=f"Locate audio file for {Path(path).name}",
                initialdir=str(Path(path).parent),
                filetypes=[
                    ("Audio", ("*.wav", "*.bwf", "*.flac", "*.mp3", "*.aac",
                               "*.m4a", "*.ogg", "*.opus", "*.aiff", "*.aif",
                               "*.dff", "*.dsf", "*.wv", "*.ape", "*.wma")),
                    ("All files", "*.*"),
                ],
            )
            if not chosen:
                # User cancelled: load markers only (no audio)
                self._apply_cue_markers(tracks)
                self._set_status(
                    f"Loaded {len(tracks)} tracks from {Path(path).name}"
                    " (no audio file loaded)")
                return
            audio_path = Path(chosen)

        # Case 3: load the resolved/chosen audio file, defer marker apply
        self._pending_cue_tracks = tracks
        self.var_src.set(str(audio_path))
        self.config_obj["last_input_dir"] = str(audio_path.parent)
        self.config_obj.save()
        self._on_load()

    def _apply_cue_markers(self, tracks: list) -> None:
        """Replace current split markers with those from a parsed CUE track list."""
        self._markers.clear()
        self._track_names.clear()
        self._track_colors.clear()
        # _track_names[i] = name of the i-th track (index 0 is the first region)
        for t in tracks:
            self._track_names.append(
                t.get("title") or f"Track {t['number']:02d}")
        for t in tracks[1:]:
            self._markers.append(t["index01"])
        self._redraw()
        self._update_mc()

    def _save_cue_dialog(self) -> None:
        src = self.var_src.get().strip()
        if not src:
            messagebox.showwarning("Trader's Little Jedi", "Load a source file first.")
            return
        f = filedialog.asksaveasfilename(
            parent=self, title="Save CUE sheet",
            initialdir=str(Path(src).parent),
            initialfile=Path(src).stem + ".cue",
            defaultextension=".cue",
            filetypes=[("CUE sheets", "*.cue"), ("All files", "*.*")],
        )
        if f:
            self._write_cue(f, src)

    def _write_cue(self, cue_path: str, src: str) -> None:
        lines: list[str] = [f'FILE "{Path(src).name}" WAVE']
        all_starts = [0.0] + list(self._markers)
        for i, t_start in enumerate(all_starts, 1):
            # i is 1-based; _track_names uses 0-based index
            name = (self._track_names[i - 1]
                    if i - 1 < len(self._track_names) and self._track_names[i - 1]
                    else f"Track {i:02d}")
            mm = int(t_start // 60)
            ss = int(t_start % 60)
            ff = int((t_start % 1) * 75)
            lines += [
                f"  TRACK {i:02d} AUDIO",
                f'    TITLE "{name}"',
                f"    INDEX 01 {mm:02d}:{ss:02d}:{ff:02d}",
            ]
        try:
            Path(cue_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Trader's Little Jedi", f"Cannot save CUE:\n{e}")
            return
        self._set_status(f"CUE saved → {Path(cue_path).name}")

    # ------------------------------------------------------------------ #
    # Playback
    # ------------------------------------------------------------------ #

    def _pb_toggle(self, _event=None) -> None:
        """Space bar: toggle play / pause (ignored when a text entry has focus)."""
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, ttk.Treeview)):
            return
        if self._playing:
            self._pb_pause()
        else:
            self._pb_play()

    def _pb_play(self) -> None:
        src = self.var_src.get().strip()
        if not src or not Path(src).is_file():
            messagebox.showwarning("Trader's Little Jedi", "Load a source file first.")
            return
        if self._playing:
            return

        ffplay = _ffplay_path(self.config_obj)
        if not ffplay.exists():
            messagebox.showwarning(
                "Trader's Little Jedi",
                "ffplay.exe not found.\n"
                "It should live next to ffmpeg.exe in the tools\\ folder.")
            return

        pos = max(0.0, min(self._playpos, self._duration))
        vol = max(0, min(int(self.var_vol.get()), 150))
        try:
            self._ffplay = subprocess.Popen(
                [str(ffplay), "-nodisp", "-autoexit",
                 "-ss", f"{pos:.3f}",
                 "-volume", str(vol),
                 src],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            messagebox.showerror("Trader's Little Jedi", f"ffplay error: {e}")
            return

        self._playing  = True
        self._play_t0  = time.time()
        self._play_p0  = pos
        self._tick_playhead()

    def _pb_pause(self) -> None:
        if not self._playing:
            return
        elapsed = time.time() - self._play_t0
        self._playpos = min(self._play_p0 + elapsed, self._duration)
        self._stop_ffplay()
        self._playing = False
        self._redraw()

    def _pb_stop(self) -> None:
        self._stop_ffplay()
        self._playing = False
        self._playpos = 0.0
        self._update_pos_display(0.0)
        self._redraw()

    def _stop_ffplay(self) -> None:
        if self._ffplay:
            try:
                self._ffplay.kill()
                self._ffplay.wait(timeout=2)
            except Exception:
                pass
            self._ffplay = None

    def _pb_begin(self) -> None:
        self._pb_stop()

    def _pb_end(self) -> None:
        self._pb_pause()
        self._playpos = self._duration
        self._update_pos_display(self._playpos)
        self._redraw()

    def _pb_back(self) -> None:
        was = self._playing
        if was:
            self._pb_pause()
        self._playpos = max(0.0, self._playpos - 10.0)
        self._update_pos_display(self._playpos)
        self._redraw()
        if was:
            self._pb_play()

    def _pb_fwd(self) -> None:
        was = self._playing
        if was:
            self._pb_pause()
        self._playpos = min(self._duration, self._playpos + 10.0)
        self._update_pos_display(self._playpos)
        self._redraw()
        if was:
            self._pb_play()

    def _pb_prev_marker(self) -> None:
        was = self._playing
        if was:
            self._pb_pause()
        before = [m for m in self._markers if m < self._playpos - 0.1]
        self._playpos = before[-1] if before else 0.0
        self._update_pos_display(self._playpos)
        self._redraw()
        if was:
            self._pb_play()

    def _pb_next_marker(self) -> None:
        was = self._playing
        if was:
            self._pb_pause()
        after = [m for m in self._markers if m > self._playpos + 0.1]
        self._playpos = after[0] if after else self._duration
        self._update_pos_display(self._playpos)
        self._redraw()
        if was:
            self._pb_play()

    def _tick_playhead(self) -> None:
        if not self._playing:
            return
        if self._ffplay and self._ffplay.poll() is not None:
            self._playing = False
            self._ffplay  = None
            self._playpos = min(self._play_p0 +
                                (time.time() - self._play_t0),
                                self._duration)
            self._redraw()
            return
        elapsed = time.time() - self._play_t0
        pos = min(self._play_p0 + elapsed, self._duration)
        self._playpos = pos
        self._update_pos_display(pos)
        vd = self._view_dur()
        if pos > self._view_start + vd * 0.9:
            self._view_start = min(pos - vd * 0.1, self._duration - vd)
        self._redraw()
        self.after(100, self._tick_playhead)

    def _update_pos_display(self, t: float) -> None:
        self.lbl_pos.configure(text=sec_to_hms(t))
        frame = int((t % 1.0) * 75)   # CD frame within current second (0–74)
        self.lbl_frame.configure(text=f"f:{frame:02d}")

    # ------------------------------------------------------------------ #
    # Keyboard cursor navigation
    # ------------------------------------------------------------------ #

    def _key_step(self, event) -> None:
        """Arrow-key cursor navigation. Ignored when a text entry has focus."""
        focused = self.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry)):
            return

        shift = bool(event.state & 0x0001)
        # Plain arrows: 1 second;  Shift+arrows: 1 CD frame (1/75 s ≈ 13 ms)
        step = (1.0 / 75.0) if shift else 1.0

        key = event.keysym
        if key in ("Left", "Up"):
            self._move_cursor(-step)
        elif key in ("Right", "Down"):
            self._move_cursor(step)

    def _move_cursor(self, delta: float) -> None:
        """Shift the playhead by delta seconds, auto-scrolling the view."""
        if self._duration <= 0:
            return
        was_playing = self._playing
        if was_playing:
            self._pb_pause()
        self._playpos = max(0.0, min(self._duration, self._playpos + delta))
        self._update_pos_display(self._playpos)
        # Auto-scroll to keep the cursor visible
        vd = self._view_dur()
        if self._playpos < self._view_start:
            self._view_start = max(0.0, self._playpos - vd * 0.1)
        elif self._playpos > self._view_start + vd:
            self._view_start = min(
                max(0.0, self._duration - vd), self._playpos - vd * 0.9)
        self._redraw()
        if was_playing:
            self._pb_play()

    # ------------------------------------------------------------------ #
    # Output / encode
    # ------------------------------------------------------------------ #

    def _pick_outdir(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select output folder",
            initialdir=self.config_obj.get("last_output_dir") or None,
        )
        if d:
            self.var_outdir.set(d)
            self.config_obj["last_output_dir"] = d
            self.config_obj.save()

    def _out_ext(self) -> str:
        return {"FLAC": ".flac", "WAV": ".wav", "MP3": ".mp3",
                "DFF (copy)": ".dff"}.get(self.var_fmt.get(), ".flac")

    def _is_copy_fmt(self) -> bool:
        return self.var_fmt.get() == "DFF (copy)"

    def _encode_args(self, ffmpeg: Path, src: str, out: str,
                     t_start: Optional[float], t_end: Optional[float],
                     gain_db: float) -> list[str]:
        args: list[str] = [str(ffmpeg), "-y", "-hide_banner"]
        if t_start is not None:
            args += ["-ss", f"{t_start:.6f}"]
        args += ["-i", src]
        if t_end is not None:
            args += ["-t", f"{t_end - (t_start or 0.0):.6f}"]

        if self._is_copy_fmt():
            args += ["-c", "copy"]
            args.append(out)
            return args

        if abs(gain_db) > 0.001:
            args += ["-af", f"volume={gain_db:.2f}dB"]

        fmt  = self.var_fmt.get()
        sr   = self.var_sr.get()
        bits = self.var_bits.get()
        if sr != "Auto":
            args += ["-ar", sr]

        if fmt == "FLAC":
            level = self.config_obj.get("flac_compression_level", 8)
            if bits == "16":
                args += ["-c:a", "flac", "-sample_fmt", "s16",
                         "-compression_level", str(level)]
            elif bits in ("24", "32"):
                args += ["-c:a", "flac", "-sample_fmt", "s32",
                         "-bits_per_raw_sample", bits,
                         "-compression_level", str(level)]
            else:
                args += ["-c:a", "flac", "-compression_level", str(level)]
        elif fmt == "WAV":
            pcm = {"16": "pcm_s16le", "24": "pcm_s24le",
                   "32": "pcm_s32le"}.get(bits, "pcm_s24le")
            args += ["-c:a", pcm]
        else:  # MP3
            args += ["-c:a", "libmp3lame", "-q:a", "0"]

        args.append(out)
        return args

    def _validate(self) -> tuple[Optional[str], Optional[Path],
                                  Optional[float], Optional[float], float]:
        src = self.var_src.get().strip()
        if not src or not Path(src).is_file():
            messagebox.showwarning("Trader's Little Jedi", "Load a source file first.")
            return None, None, None, None, 0.0
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror("Trader's Little Jedi", "ffmpeg.exe not found.")
            return None, None, None, None, 0.0
        ss = self.var_start.get().strip()
        es = self.var_end.get().strip()
        t_s = parse_time_str(ss) if ss else None
        t_e = parse_time_str(es) if es else None
        if ss and t_s is None:
            messagebox.showerror("Trader's Little Jedi",
                                 f"Cannot parse start time: '{ss}'")
            return None, None, None, None, 0.0
        if es and t_e is None:
            messagebox.showerror("Trader's Little Jedi",
                                 f"Cannot parse end time: '{es}'")
            return None, None, None, None, 0.0
        if t_s and t_e and t_e <= t_s:
            messagebox.showerror("Trader's Little Jedi", "End time must be after start.")
            return None, None, None, None, 0.0
        return src, ffmpeg, t_s, t_e, float(self.var_gain.get())

    # ------------------------------------------------------------------ #
    # Export selection
    # ------------------------------------------------------------------ #

    def _export_selection(self) -> None:
        src, ffmpeg, t_s, t_e, gain = self._validate()
        if src is None:
            return
        outdir = self.var_outdir.get().strip() or str(Path(src).parent)
        f = filedialog.asksaveasfilename(
            parent=self, title="Save output file",
            initialfile=Path(src).stem + self._out_ext(),
            initialdir=outdir, defaultextension=self._out_ext(),
        )
        if not f:
            return
        Path(f).parent.mkdir(parents=True, exist_ok=True)
        args = self._encode_args(ffmpeg, src, f, t_s, t_e, gain)
        if not self.runner.run_sequence([args]):
            messagebox.showwarning("Trader's Little Jedi",
                                   "Another job is already running.")
            return
        self.destroy()

    # ------------------------------------------------------------------ #
    # Track out
    # ------------------------------------------------------------------ #

    def _track_out(self) -> None:
        src, ffmpeg, _ts, _te, gain = self._validate()
        if src is None:
            return
        dur = self._duration
        if dur <= 0:
            messagebox.showwarning("Trader's Little Jedi",
                                   "Load a file first (duration unknown).")
            return
        outdir = self.var_outdir.get().strip()
        if not outdir:
            messagebox.showwarning("Trader's Little Jedi",
                                   "Choose an output folder first.")
            return

        raw = sorted({0.0} | set(self._markers) | {dur})
        bounds: list[float] = [raw[0]]
        for b in raw[1:]:
            if b - bounds[-1] >= 0.1:
                bounds.append(b)

        segs = [(bounds[i], bounds[i + 1])
                for i in range(len(bounds) - 1)]
        n = len(segs)
        if n < 1:
            messagebox.showwarning("Trader's Little Jedi", "No segments to export.")
            return

        if not messagebox.askyesno(
            "Trader's Little Jedi",
            f"Export {n} track{'s' if n != 1 else ''} to:\n{outdir}\n\n"
            f"Format: {self.var_fmt.get()}\n\nContinue?",
        ):
            return

        Path(outdir).mkdir(parents=True, exist_ok=True)
        stem = Path(src).stem
        ext  = self._out_ext()
        jobs: list[list[str]] = []

        for i, (t0, t1) in enumerate(segs, 1):
            # i is 1-based; _track_names uses 0-based index
            name = (self._track_names[i - 1]
                    if i - 1 < len(self._track_names) and self._track_names[i - 1]
                    else f"{stem}_{i:03d}")
            out  = str(Path(outdir) / f"{name}{ext}")
            args = self._encode_args(
                ffmpeg, src, out,
                t_start=t0 if t0 > 0.001 else None,
                t_end=t1   if t1 < dur - 0.001 else None,
                gain_db=gain,
            )
            jobs.append(args)

        if not self.runner.run_sequence(jobs):
            messagebox.showwarning("Trader's Little Jedi",
                                   "Another job is already running.")
            return
        self._set_status(f"Track-out started: {n} tracks → {outdir}")
        self.destroy()

    # ------------------------------------------------------------------ #

    def _set_status(self, text: str) -> None:
        self.after(0, lambda: self.lbl_status.configure(text=text))

    def _close(self) -> None:
        self._load_token += 1   # abort any in-progress decode
        self._pb_stop()
        self.destroy()
