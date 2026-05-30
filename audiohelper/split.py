"""Split a single audio file into segments via ffmpeg.

Three modes
-----------
Cue sheet   — parse an .cue file's INDEX 01 timecodes into tracks
Time list   — user types one breakpoint per line (HH:MM:SS.mmm)
Fixed       — every N minutes + M seconds (ffmpeg segment muxer)
"""

import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from .tools import get_tool

_OUT_OPTIONS = ("Keep source format", "WAV (16-bit PCM)", "WAV (24-bit PCM)", "FLAC")
_OUT_MAP: dict[str, tuple[str, Optional[str]]] = {
    "Keep source format":  ("",      None),
    "WAV (16-bit PCM)":    (".wav",  "pcm_s16le"),
    "WAV (24-bit PCM)":    (".wav",  "pcm_s24le"),
    "FLAC":                (".flac", "flac"),
}


# ---------------------------------------------------------------
# CUE sheet parser
# ---------------------------------------------------------------

def _cue_mm_ss_ff(mm: str, ss: str, ff: str) -> float:
    return int(mm) * 60.0 + int(ss) + int(ff) / 75.0


def parse_cue_file_ref(text: str) -> Optional[str]:
    """Return the filename from the first FILE directive in a CUE sheet, or None."""
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'FILE\s+"(.+?)"\s+\w+', line, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.match(r'FILE\s+(.+?)\s+(WAVE|MP3|AIFF|BINARY|MOTOROLA)\s*$', line, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def parse_cue(text: str) -> list[dict]:
    """Parse cue sheet text → list of track dicts with index01 (seconds)."""
    tracks: list[dict] = []
    cur: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r'TRACK\s+(\d+)\s+AUDIO', line, re.IGNORECASE)
        if m:
            if cur:
                tracks.append(cur)
            cur = {"number": int(m.group(1)), "title": "", "performer": "",
                   "index01": None, "index00": None}
            continue
        m = re.match(r'TITLE\s+"(.*)"', line, re.IGNORECASE)
        if m and cur:
            cur["title"] = m.group(1)
            continue
        m = re.match(r'PERFORMER\s+"(.*)"', line, re.IGNORECASE)
        if m and cur:
            cur["performer"] = m.group(1)
            continue
        m = re.match(r'INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)', line, re.IGNORECASE)
        if m and cur:
            idx = int(m.group(1))
            t = _cue_mm_ss_ff(m.group(2), m.group(3), m.group(4))
            if idx == 1:
                cur["index01"] = t
            elif idx == 0:
                cur["index00"] = t
    if cur:
        tracks.append(cur)
    return [t for t in tracks if t["index01"] is not None]


# ---------------------------------------------------------------
# Time string helpers
# ---------------------------------------------------------------

def parse_time_str(s: str) -> Optional[float]:
    """HH:MM:SS.mmm or MM:SS.mmm or SS.mmm → seconds. None on failure."""
    s = s.strip()
    m = re.fullmatch(r'(\d+):(\d+):(\d+(?:\.\d+)?)', s)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.fullmatch(r'(\d+):(\d+(?:\.\d+)?)', s)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.fullmatch(r'(\d+(?:\.\d+)?)', s)
    if m:
        return float(m.group(1))
    return None


def sec_to_hms(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


# ---------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------

class SplitDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Split audio file")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("760x680")

        self._cue_tracks: list[dict] = []

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ----- source file -----
        src_f = ttk.LabelFrame(frm, text="Source file", padding=8)
        src_f.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.var_src = tk.StringVar()
        ttk.Entry(src_f, textvariable=self.var_src, width=60).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(src_f, text="…", width=3, command=self._pick_source).grid(row=0, column=1)
        src_f.columnconfigure(0, weight=1)

        # ----- mode tabs -----
        self.nb = ttk.Notebook(frm)
        self.nb.grid(row=1, column=0, sticky="nsew", pady=4)

        self._build_cue_tab()
        self._build_time_tab()
        self._build_fixed_tab()

        # ----- output options -----
        out_f = ttk.LabelFrame(frm, text="Output options", padding=8)
        out_f.grid(row=2, column=0, sticky="ew", pady=(6, 0))

        ttk.Label(out_f, text="Format:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_out_fmt = tk.StringVar(value="Keep source format")
        ttk.Combobox(out_f, textvariable=self.var_out_fmt, state="readonly", width=20,
                     values=list(_OUT_OPTIONS)).grid(row=0, column=1, sticky="w", padx=6)

        ttk.Label(out_f, text="Output folder:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_outdir = tk.StringVar()
        ttk.Entry(out_f, textvariable=self.var_outdir, width=50).grid(
            row=1, column=1, sticky="ew", padx=6)
        ttk.Button(out_f, text="…", width=3, command=self._pick_outdir).grid(row=1, column=2)
        out_f.columnconfigure(1, weight=1)

        act = ttk.Frame(frm)
        act.grid(row=3, column=0, sticky="e", pady=(10, 0))
        ttk.Button(act, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(act, text="Start", command=self._start).pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        if initial_files:
            from .action_picker import AUDIO_EXTS
            audio = [f for f in initial_files
                     if Path(f).is_file() and Path(f).suffix.lower() in AUDIO_EXTS]
            if audio:
                self.var_src.set(audio[0])
                self.var_outdir.set(str(Path(audio[0]).parent))
                self._auto_detect_cue(Path(audio[0]))

    # ----- tab builders -----

    def _build_cue_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Cue sheet")

        ttk.Label(tab, text="Cue file:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_cue = tk.StringVar()
        ttk.Entry(tab, textvariable=self.var_cue, width=50).grid(
            row=0, column=1, sticky="ew", padx=6)
        ttk.Button(tab, text="…", width=3, command=self._pick_cue).grid(row=0, column=2)
        ttk.Button(tab, text="Load cue", command=self._load_cue).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 6))

        cols = ("track", "title", "start", "end")
        self.cue_tv = ttk.Treeview(tab, columns=cols, show="headings", height=8)
        for c, w in zip(cols, (50, 340, 90, 90)):
            self.cue_tv.heading(c, text=c.title())
            self.cue_tv.column(c, width=w, anchor="w")
        cue_ys = ttk.Scrollbar(tab, orient="vertical", command=self.cue_tv.yview)
        self.cue_tv.configure(yscrollcommand=cue_ys.set)
        cue_ys.grid(row=2, column=3, sticky="ns")
        self.cue_tv.grid(row=2, column=0, columnspan=3, sticky="nsew")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(2, weight=1)

    def _build_time_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Time list")

        ttk.Label(tab,
                  text="One break-point per line.  Format: HH:MM:SS.mmm or MM:SS.mmm",
                  foreground="gray").pack(anchor="w")
        ttk.Label(tab,
                  text="Example:  05:30.000   →  split at 5 min 30 sec",
                  foreground="gray").pack(anchor="w", pady=(0, 4))

        inner = ttk.Frame(tab)
        inner.pack(fill="both", expand=True)
        self.txt_times = tk.Text(inner, height=9, width=30, font=("Consolas", 9))
        time_sb = ttk.Scrollbar(inner, orient="vertical", command=self.txt_times.yview)
        self.txt_times.configure(yscrollcommand=time_sb.set)
        time_sb.pack(side="right", fill="y")
        self.txt_times.pack(fill="both", expand=True)

        ttk.Button(tab, text="Validate", command=self._validate_times).pack(
            anchor="w", pady=(6, 0))
        self.lbl_time_info = ttk.Label(tab, text="", foreground="gray")
        self.lbl_time_info.pack(anchor="w", pady=(2, 0))

    def _build_fixed_tab(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Fixed duration")

        ttk.Label(tab, text="Split every:").grid(row=0, column=0, sticky="w", pady=6)
        self.var_fixed_min = tk.IntVar(value=5)
        ttk.Spinbox(tab, from_=0, to=999, textvariable=self.var_fixed_min, width=6).grid(
            row=0, column=1, sticky="w", padx=4)
        ttk.Label(tab, text="min").grid(row=0, column=2, sticky="w")
        self.var_fixed_sec = tk.IntVar(value=0)
        ttk.Spinbox(tab, from_=0, to=59, textvariable=self.var_fixed_sec, width=6).grid(
            row=0, column=3, sticky="w", padx=4)
        ttk.Label(tab, text="sec").grid(row=0, column=4, sticky="w")
        ttk.Label(tab,
                  text="Segments named:  001.ext  002.ext  …",
                  foreground="gray").grid(row=1, column=0, columnspan=5, sticky="w", pady=4)

    # ----- source / cue pickers -----

    def _pick_source(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Select audio file to split",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio files", ("*.wav", "*.bwf", "*.flac", "*.mp3", "*.ape",
                                 "*.dff", "*.dsf", "*.m4a", "*.aiff", "*.aif",
                                 "*.wv", "*.tta", "*.ogg", "*.opus")),
                ("All files", "*.*"),
            ],
        )
        if not f:
            return
        self.var_src.set(f)
        if not self.var_outdir.get():
            self.var_outdir.set(str(Path(f).parent))
        self._auto_detect_cue(Path(f))
        self.config_obj["last_input_dir"] = str(Path(f).parent)
        self.config_obj.save()

    def _auto_detect_cue(self, src: Path) -> None:
        if self.var_cue.get():
            return
        candidates = [src.with_suffix(".cue")] + list(src.parent.glob("*.cue"))
        for c in candidates:
            if c.is_file():
                self.var_cue.set(str(c))
                self._load_cue()
                return

    def _pick_cue(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Select cue sheet",
            filetypes=[("Cue sheets", "*.cue"), ("All files", "*.*")],
        )
        if f:
            self.var_cue.set(f)
            self._load_cue()

    def _load_cue(self) -> None:
        path = self.var_cue.get().strip()
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            messagebox.showerror("Trader's Little Jedi", f"Cannot read cue file:\n{e}")
            return
        tracks = parse_cue(text)
        if not tracks:
            messagebox.showwarning("Trader's Little Jedi",
                                   "No AUDIO tracks with INDEX 01 found in the cue file.")
            return
        self._cue_tracks = tracks
        for iid in self.cue_tv.get_children():
            self.cue_tv.delete(iid)
        for i, t in enumerate(tracks):
            start = sec_to_hms(t["index01"])
            end = sec_to_hms(tracks[i + 1]["index01"]) if i + 1 < len(tracks) else "EOF"
            self.cue_tv.insert("", "end", values=(
                f"{t['number']:02d}",
                t["title"] or f"Track {t['number']:02d}",
                start,
                end,
            ))
        # Switch to cue tab
        self.nb.select(0)

    def _pick_outdir(self) -> None:
        d = filedialog.askdirectory(parent=self, title="Output folder",
                                    initialdir=self.var_outdir.get() or None)
        if d:
            self.var_outdir.set(d)

    # ----- time list validation -----

    def _validate_times(self) -> tuple[list[float], bool]:
        raw = self.txt_times.get("1.0", "end")
        times: list[float] = []
        errors: list[str] = []
        for i, line in enumerate(raw.splitlines(), 1):
            s = line.strip()
            if not s:
                continue
            t = parse_time_str(s)
            if t is None:
                errors.append(f"Line {i}: unrecognised '{s}'")
            else:
                times.append(t)
        if errors:
            self.lbl_time_info.configure(
                text="Errors: " + ";  ".join(errors), foreground="#b03030")
            return times, False
        if not times:
            self.lbl_time_info.configure(text="No break-points entered.", foreground="gray")
            return times, True
        preview = f"{len(times)} break-point(s) → {len(times) + 1} segment(s)"
        preview += "   " + ",  ".join(sec_to_hms(t) for t in times[:4])
        if len(times) > 4:
            preview += " …"
        self.lbl_time_info.configure(text=preview, foreground="gray")
        return times, True

    # ----- job builders -----

    def _codec_args(self, codec: Optional[str]) -> list[str]:
        if codec is None:
            return ["-c", "copy"]
        if codec == "flac":
            level = self.config_obj.get("flac_compression_level", 8)
            return ["-c:a", "flac", "-compression_level", str(level)]
        return ["-c:a", codec]

    def _build_cue_jobs(self, src: Path, out_dir: Path,
                        out_ext: str, out_codec: Optional[str],
                        ffmpeg: Path) -> list:
        if not self._cue_tracks:
            messagebox.showwarning("Trader's Little Jedi", "Load a cue sheet first.")
            return []
        jobs = []
        tracks = self._cue_tracks
        effective_ext = out_ext or src.suffix.lower()
        for i, t in enumerate(tracks):
            start = t["index01"]
            title = (t.get("title") or f"Track{t['number']:02d}")
            safe = re.sub(r'[\\/:*?"<>|]', "_", title).strip()
            name = f"{t['number']:02d} - {safe}{effective_ext}"
            out_path = out_dir / name

            args = [str(ffmpeg), "-y", "-hide_banner",
                    "-ss", f"{start:.6f}", "-i", str(src)]
            if i + 1 < len(tracks):
                args += ["-to", f"{tracks[i + 1]['index01']:.6f}"]
            args += self._codec_args(out_codec)
            args += ["-map", "0:a", "-map_metadata", "0"]
            args.append(str(out_path))
            jobs.append(args)
        return jobs

    def _build_time_jobs(self, src: Path, out_dir: Path,
                         out_ext: str, out_codec: Optional[str],
                         ffmpeg: Path, times: list[float]) -> list:
        breaks = sorted(times)
        # Build (start, end_or_None) pairs
        segments = [(0.0, breaks[0])]
        for j in range(len(breaks)):
            end = breaks[j + 1] if j + 1 < len(breaks) else None
            segments.append((breaks[j], end))
        effective_ext = out_ext or src.suffix.lower()
        jobs = []
        for idx, (start, end) in enumerate(segments, 1):
            out_path = out_dir / f"{idx:03d}{effective_ext}"
            args = [str(ffmpeg), "-y", "-hide_banner",
                    "-ss", f"{start:.6f}", "-i", str(src)]
            if end is not None:
                args += ["-to", f"{end:.6f}"]
            args += self._codec_args(out_codec)
            args += ["-map", "0:a"]
            args.append(str(out_path))
            jobs.append(args)
        return jobs

    def _build_fixed_jobs(self, src: Path, out_dir: Path,
                          out_ext: str, out_codec: Optional[str],
                          ffmpeg: Path, segment_sec: int) -> list:
        effective_ext = out_ext or src.suffix.lower()
        pattern = str(out_dir / f"%03d{effective_ext}")
        args = [str(ffmpeg), "-y", "-hide_banner", "-i", str(src),
                "-f", "segment", "-segment_time", str(segment_sec),
                "-reset_timestamps", "1"]
        args += self._codec_args(out_codec)
        args.append(pattern)
        return [args]

    # ----- start -----

    def _start(self) -> None:
        src_str = self.var_src.get().strip()
        if not src_str:
            messagebox.showwarning("Trader's Little Jedi", "Select a source file first.")
            return
        src = Path(src_str)
        if not src.is_file():
            messagebox.showerror("Trader's Little Jedi", f"Source file not found:\n{src}")
            return

        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                "ffmpeg.exe not found. Open Tools → Update all CLI tools to install.",
            )
            return

        out_str = self.var_outdir.get().strip()
        if not out_str:
            messagebox.showwarning("Trader's Little Jedi", "Choose an output folder.")
            return
        out_dir = Path(out_str)
        out_dir.mkdir(parents=True, exist_ok=True)

        fmt_choice = self.var_out_fmt.get()
        out_ext, out_codec = _OUT_MAP[fmt_choice]

        tab = self.nb.index("current")

        if tab == 0:  # cue
            jobs = self._build_cue_jobs(src, out_dir, out_ext, out_codec, ffmpeg)
        elif tab == 1:  # time list
            times, ok = self._validate_times()
            if not ok:
                messagebox.showwarning("Trader's Little Jedi",
                                       "Fix the time list errors before starting.")
                return
            if not times:
                messagebox.showwarning("Trader's Little Jedi",
                                       "Enter at least one break-point time.")
                return
            jobs = self._build_time_jobs(src, out_dir, out_ext, out_codec, ffmpeg, times)
        else:  # fixed
            mins = int(self.var_fixed_min.get())
            secs = int(self.var_fixed_sec.get())
            total_sec = mins * 60 + secs
            if total_sec <= 0:
                messagebox.showwarning("Trader's Little Jedi",
                                       "Segment duration must be greater than 0 seconds.")
                return
            jobs = self._build_fixed_jobs(src, out_dir, out_ext, out_codec, ffmpeg, total_sec)

        if not jobs:
            return
        if not self.runner.run_sequence(jobs):
            messagebox.showwarning("Trader's Little Jedi", "Another job is already running.")
            return
        self.destroy()
