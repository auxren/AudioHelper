"""Batch Converter — XLD/XACT-style multi-file format conversion with presets.

Converts any ffmpeg-readable audio to FLAC / WAV / MP3 / AAC / Ogg / AIFF.
Named presets are stored in config.json so settings persist across sessions.

Workflow:
  1. Add files or folders (drag-drop or buttons).
  2. Pick a preset or configure format + quality manually.
  3. Set output folder and post-processing options.
  4. Click Convert — per-file progress with colored status log.
"""

import json
import os
import subprocess
import threading
import tkinter as tk
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import theme as _t
from .action_picker import AUDIO_EXTS
from .tc_tagger import mutagen_available, write_tags as mutagen_write_tags
from .tools import get_tool

# ── Preset data model ─────────────────────────────────────────────────────────

@dataclass
class ConversionPreset:
    name: str
    fmt: str          = "FLAC"   # FLAC WAV MP3 AAC OGG AIFF
    flac_level: int   = 8
    mp3_mode: str     = "VBR"    # CBR ABR VBR
    mp3_vbr_q: int    = 0        # 0=best … 9=worst
    mp3_cbr_br: int   = 320      # kbps
    mp3_abr_br: int   = 192
    aac_br: int       = 256      # kbps
    ogg_q: float      = 6.0      # -1…10
    wav_bits: int     = 16       # 16 24 32
    post_test: bool   = True
    post_checksum: bool = False
    post_delete: bool = False
    output_mode: str  = "source"  # source | custom
    output_dir: str   = ""


BUILTIN_PRESETS: list[ConversionPreset] = [
    ConversionPreset("Archive FLAC 8",   fmt="FLAC",  flac_level=8,
                     post_test=True,  post_checksum=True),
    ConversionPreset("Lossless WAV",     fmt="WAV",   wav_bits=24),
    ConversionPreset("Portable MP3 V0",  fmt="MP3",   mp3_mode="VBR", mp3_vbr_q=0),
    ConversionPreset("MP3 320 CBR",      fmt="MP3",   mp3_mode="CBR", mp3_cbr_br=320),
    ConversionPreset("AAC 256 kbps",     fmt="AAC",   aac_br=256),
    ConversionPreset("Ogg Vorbis q6",    fmt="OGG",   ogg_q=6.0),
    ConversionPreset("AIFF 16-bit",      fmt="AIFF",  wav_bits=16),
]

FORMATS = ["FLAC", "WAV", "MP3", "AAC", "OGG", "AIFF"]
OUTPUT_EXT = {
    "FLAC": ".flac", "WAV": ".wav", "MP3": ".mp3",
    "AAC": ".m4a",  "OGG": ".ogg", "AIFF": ".aiff",
}


# ── Config helpers ────────────────────────────────────────────────────────────

def _load_presets(config) -> list[ConversionPreset]:
    raw = config.get("batch_presets", None)
    if not raw:
        return list(BUILTIN_PRESETS)
    try:
        return [ConversionPreset(**d) for d in raw]
    except Exception:
        return list(BUILTIN_PRESETS)


def _save_presets(config, presets: list[ConversionPreset]) -> None:
    config["batch_presets"] = [asdict(p) for p in presets]
    config.save()


# ── ffmpeg command builder ────────────────────────────────────────────────────

def _build_cmd(ffmpeg: Path, src: Path, dst: Path,
               preset: ConversionPreset) -> list[str]:
    cmd = [str(ffmpeg), "-y", "-hide_banner", "-i", str(src)]
    fmt = preset.fmt
    if fmt == "FLAC":
        cmd += ["-c:a", "flac", "-compression_level", str(preset.flac_level)]
    elif fmt == "WAV":
        depth = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le"}.get(
            preset.wav_bits, "pcm_s16le")
        cmd += ["-c:a", depth]
    elif fmt == "AIFF":
        depth = {16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_f32le"}.get(
            preset.wav_bits, "pcm_s16le")
        cmd += ["-c:a", depth, "-f", "aiff"]
    elif fmt == "MP3":
        cmd += ["-c:a", "libmp3lame"]
        if preset.mp3_mode == "VBR":
            cmd += ["-q:a", str(preset.mp3_vbr_q)]
        elif preset.mp3_mode == "CBR":
            cmd += ["-b:a", f"{preset.mp3_cbr_br}k"]
        else:  # ABR
            cmd += ["-abr", "1", "-b:a", f"{preset.mp3_abr_br}k"]
    elif fmt == "AAC":
        cmd += ["-c:a", "aac", "-b:a", f"{preset.aac_br}k"]
    elif fmt == "OGG":
        cmd += ["-c:a", "libvorbis", "-q:a", str(preset.ogg_q)]
    cmd.append(str(dst))
    return cmd


def _test_cmd(ffmpeg: Path, path: Path) -> list[str]:
    return [str(ffmpeg), "-v", "error", "-i", str(path), "-f", "null", "-"]


# ── Main dialog ───────────────────────────────────────────────────────────────

class BatchConvertDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Batch Converter")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("1060x700")
        self.minsize(860, 540)

        self._files: list[Path] = []
        self._presets = _load_presets(config)
        self._current_preset_idx = 0
        self._running = False

        _t.apply(self)
        self._build_preset_bar()
        self._build_body()
        self._build_bottom()
        self.status = ttk.Label(self, text="Add files to begin.",
                                anchor="w", style="Status.TLabel")
        self.status.pack(fill="x", side="bottom")

        self._load_preset(0)

        if initial_files:
            self._add_paths([Path(f) for f in initial_files])

    # ── Preset bar ────────────────────────────────────────────────────────────

    def _build_preset_bar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="Preset:").pack(side="left")
        self.var_preset = tk.StringVar()
        self.preset_combo = ttk.Combobox(
            bar, textvariable=self.var_preset, state="readonly", width=28)
        self.preset_combo.pack(side="left", padx=4)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_select)
        ttk.Button(bar, text="Save", command=self._save_preset).pack(side="left", padx=2)
        ttk.Button(bar, text="New…", command=self._new_preset).pack(side="left", padx=2)
        ttk.Button(bar, text="Delete", style="Danger.TButton",
                   command=self._delete_preset).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Add files…",
                   command=self._add_files_dialog).pack(side="left")
        ttk.Button(bar, text="Add folder…",
                   command=self._add_folder_dialog).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear all", style="Danger.TButton",
                   command=self._clear).pack(side="left")
        self._refresh_preset_combo()

    # ── Body ──────────────────────────────────────────────────────────────────

    def _build_body(self) -> None:
        body = ttk.Frame(self, padding=(8, 0, 8, 0))
        body.pack(fill="both", expand=True)
        paned = ttk.PanedWindow(body, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # ── Left: file list + post-processing ─────────────────────────────
        left = ttk.Frame(paned)

        fl = ttk.LabelFrame(left, text="Input files", padding=4)
        fl.pack(fill="both", expand=True)
        cols = ("status", "name", "fmt", "size")
        self.tv = ttk.Treeview(fl, columns=cols, show="headings",
                               selectmode="extended")
        self.tv.heading("status", text="",       anchor="center")
        self.tv.heading("name",   text="File",   anchor="w")
        self.tv.heading("fmt",    text="Format", anchor="w")
        self.tv.heading("size",   text="Size",   anchor="e")
        self.tv.column("status", width=28,  minwidth=28,  stretch=False, anchor="center")
        self.tv.column("name",   width=240, minwidth=100, stretch=True,  anchor="w")
        self.tv.column("fmt",    width=70,  minwidth=50,  stretch=False, anchor="w")
        self.tv.column("size",   width=70,  minwidth=50,  stretch=False, anchor="e")
        self.tv.tag_configure("ok",   foreground=_t.LOG_OK)
        self.tv.tag_configure("err",  foreground=_t.LOG_ERR)
        self.tv.tag_configure("info", foreground=_t.LOG_INFO)
        sb = ttk.Scrollbar(fl, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)

        post = ttk.LabelFrame(left, text="Post-conversion", padding=8)
        post.pack(fill="x", pady=(4, 0))
        self.var_test    = tk.BooleanVar()
        self.var_checksum= tk.BooleanVar()
        self.var_delete  = tk.BooleanVar()
        ttk.Checkbutton(post, text="Test output files for integrity",
                        variable=self.var_test).pack(anchor="w")
        ttk.Checkbutton(post, text="Create MD5 checksum file",
                        variable=self.var_checksum).pack(anchor="w")
        ttk.Checkbutton(post, text="Delete source files after conversion",
                        variable=self.var_delete).pack(anchor="w")
        paned.add(left, weight=2)

        # ── Right: format + quality + output ──────────────────────────────
        right = ttk.Frame(paned, padding=(8, 0, 0, 0))

        fmt_f = ttk.LabelFrame(right, text="Output format", padding=8)
        fmt_f.pack(fill="x")
        fmt_f.columnconfigure(1, weight=1)
        self.var_fmt = tk.StringVar(value="FLAC")
        for col, fmt in enumerate(FORMATS):
            ttk.Radiobutton(fmt_f, text=fmt, variable=self.var_fmt, value=fmt,
                            command=self._on_fmt_change).grid(
                row=col // 3, column=col % 3, sticky="w", padx=8, pady=2)

        # Quality notebook — one tab per format group
        self.q_nb = ttk.Notebook(right)
        self.q_nb.pack(fill="x", pady=(8, 0))

        # FLAC tab
        ft = ttk.Frame(self.q_nb, padding=8)
        self.q_nb.add(ft, text="FLAC")
        ttk.Label(ft, text="Compression level (0=fast, 8=smallest):").grid(
            row=0, column=0, sticky="w")
        self.var_flac_lvl = tk.IntVar(value=8)
        ttk.Combobox(ft, textvariable=self.var_flac_lvl, width=5, state="readonly",
                     values=list(range(9))).grid(row=0, column=1, sticky="w", padx=6)

        # WAV/AIFF tab
        wt = ttk.Frame(self.q_nb, padding=8)
        self.q_nb.add(wt, text="WAV / AIFF")
        ttk.Label(wt, text="Bit depth:").grid(row=0, column=0, sticky="w")
        self.var_wav_bits = tk.IntVar(value=24)
        ttk.Combobox(wt, textvariable=self.var_wav_bits, width=6, state="readonly",
                     values=[16, 24, 32]).grid(row=0, column=1, sticky="w", padx=6)

        # MP3 tab
        mt = ttk.Frame(self.q_nb, padding=8)
        self.q_nb.add(mt, text="MP3")
        mt.columnconfigure(1, weight=1)
        self.var_mp3_mode = tk.StringVar(value="VBR")
        for r, mode in enumerate(["VBR", "CBR", "ABR"]):
            ttk.Radiobutton(mt, text=mode, variable=self.var_mp3_mode, value=mode,
                            command=self._on_mp3_mode).grid(row=r, column=0, sticky="w")
        ttk.Label(mt, text="VBR quality (0=best):").grid(row=0, column=1, sticky="w", padx=8)
        self.var_mp3_vbr = tk.IntVar(value=0)
        ttk.Combobox(mt, textvariable=self.var_mp3_vbr, width=5, state="readonly",
                     values=list(range(10))).grid(row=0, column=2, sticky="w")
        ttk.Label(mt, text="CBR bitrate (kbps):").grid(row=1, column=1, sticky="w", padx=8)
        self.var_mp3_cbr = tk.IntVar(value=320)
        ttk.Combobox(mt, textvariable=self.var_mp3_cbr, width=6, state="readonly",
                     values=[96, 128, 160, 192, 224, 256, 320]).grid(row=1, column=2, sticky="w")
        ttk.Label(mt, text="ABR target (kbps):").grid(row=2, column=1, sticky="w", padx=8)
        self.var_mp3_abr = tk.IntVar(value=192)
        ttk.Spinbox(mt, from_=64, to=320, increment=16, textvariable=self.var_mp3_abr,
                    width=6).grid(row=2, column=2, sticky="w")

        # AAC tab
        at = ttk.Frame(self.q_nb, padding=8)
        self.q_nb.add(at, text="AAC")
        ttk.Label(at, text="Bitrate (kbps):").grid(row=0, column=0, sticky="w")
        self.var_aac_br = tk.IntVar(value=256)
        ttk.Combobox(at, textvariable=self.var_aac_br, width=7, state="readonly",
                     values=[96, 128, 160, 192, 224, 256, 320]).grid(
            row=0, column=1, sticky="w", padx=6)

        # Ogg tab
        ot = ttk.Frame(self.q_nb, padding=8)
        self.q_nb.add(ot, text="Ogg")
        ttk.Label(ot, text="Quality (-1…10):").grid(row=0, column=0, sticky="w")
        self.var_ogg_q = tk.DoubleVar(value=6.0)
        ttk.Spinbox(ot, from_=-1, to=10, increment=0.5,
                    textvariable=self.var_ogg_q, width=6).grid(
            row=0, column=1, sticky="w", padx=6)

        # Output folder
        out_f = ttk.LabelFrame(right, text="Output folder", padding=8)
        out_f.pack(fill="x", pady=(8, 0))
        self.var_out_mode = tk.StringVar(value="source")
        ttk.Radiobutton(out_f, text="Same folder as source files",
                        variable=self.var_out_mode, value="source").pack(anchor="w")
        row_cust = ttk.Frame(out_f)
        row_cust.pack(fill="x")
        ttk.Radiobutton(row_cust, text="Custom:",
                        variable=self.var_out_mode, value="custom").pack(side="left")
        self.var_out_dir = tk.StringVar()
        ttk.Entry(row_cust, textvariable=self.var_out_dir, width=28).pack(
            side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(row_cust, text="…", width=3,
                   command=self._browse_out).pack(side="left", padx=(4, 0))

        # Options
        opt_f = ttk.LabelFrame(right, text="Options", padding=8)
        opt_f.pack(fill="x", pady=(8, 0))
        self.var_keep_struct = tk.BooleanVar(value=True)
        self.var_skip_exists = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_f, text="Keep folder structure in output",
                        variable=self.var_keep_struct).pack(anchor="w")
        ttk.Checkbutton(opt_f, text="Skip if output file already exists",
                        variable=self.var_skip_exists).pack(anchor="w")

        paned.add(right, weight=1)

    # ── Bottom bar ────────────────────────────────────────────────────────────

    def _build_bottom(self) -> None:
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Close", command=self._on_close).pack(side="right")
        self.btn_convert = ttk.Button(bar, text="Convert All",
                                      style="Action.TButton",
                                      command=self._start,
                                      state="disabled")
        self.btn_convert.pack(side="right", padx=4)

    # ── File management ───────────────────────────────────────────────────────

    def _add_files_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(AUDIO_EXTS))
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("Audio files", exts), ("All files", "*.*")],
        )
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
            self._add_paths([Path(f) for f in files])

    def _add_folder_dialog(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        folder = Path(d)
        self.config_obj["last_input_dir"] = str(folder)
        self.config_obj.save()
        paths = sorted(p for p in folder.rglob("*")
                       if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
        self._add_paths(paths)

    def _add_paths(self, paths: list[Path]) -> None:
        added = 0
        for p in paths:
            if p not in self._files:
                self._files.append(p)
                size_kb = p.stat().st_size // 1024 if p.exists() else 0
                size_str = (f"{size_kb/1024:.1f} MB" if size_kb > 1024
                            else f"{size_kb} KB")
                self.tv.insert("", "end", values=(
                    "·", p.name, p.suffix.upper().lstrip("."), size_str))
                added += 1
        if added:
            self.btn_convert.configure(state="normal")
            self.status.configure(text=f"{len(self._files)} file(s) loaded.")

    def _clear(self) -> None:
        self._files.clear()
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        self.btn_convert.configure(state="disabled")
        self.status.configure(text="Add files to begin.")

    def _browse_out(self) -> None:
        d = filedialog.askdirectory(parent=self, title="Select output folder")
        if d:
            self.var_out_dir.set(d)
            self.var_out_mode.set("custom")

    # ── Preset management ─────────────────────────────────────────────────────

    def _refresh_preset_combo(self) -> None:
        names = [p.name for p in self._presets]
        self.preset_combo.configure(values=names)
        if names:
            self.preset_combo.current(
                min(self._current_preset_idx, len(names) - 1))

    def _load_preset(self, idx: int) -> None:
        if not self._presets or idx >= len(self._presets):
            return
        self._current_preset_idx = idx
        p = self._presets[idx]
        self.var_fmt.set(p.fmt)
        self.var_flac_lvl.set(p.flac_level)
        self.var_wav_bits.set(p.wav_bits)
        self.var_mp3_mode.set(p.mp3_mode)
        self.var_mp3_vbr.set(p.mp3_vbr_q)
        self.var_mp3_cbr.set(p.mp3_cbr_br)
        self.var_mp3_abr.set(p.mp3_abr_br)
        self.var_aac_br.set(p.aac_br)
        self.var_ogg_q.set(p.ogg_q)
        self.var_test.set(p.post_test)
        self.var_checksum.set(p.post_checksum)
        self.var_delete.set(p.post_delete)
        self.var_out_mode.set(p.output_mode)
        self.var_out_dir.set(p.output_dir)
        self._on_fmt_change()

    def _current_preset_from_ui(self, name: Optional[str] = None) -> ConversionPreset:
        existing_name = (name or
                         (self._presets[self._current_preset_idx].name
                          if self._presets else "Untitled"))
        return ConversionPreset(
            name=existing_name,
            fmt=self.var_fmt.get(),
            flac_level=int(self.var_flac_lvl.get()),
            mp3_mode=self.var_mp3_mode.get(),
            mp3_vbr_q=int(self.var_mp3_vbr.get()),
            mp3_cbr_br=int(self.var_mp3_cbr.get()),
            mp3_abr_br=int(self.var_mp3_abr.get()),
            aac_br=int(self.var_aac_br.get()),
            ogg_q=float(self.var_ogg_q.get()),
            wav_bits=int(self.var_wav_bits.get()),
            post_test=bool(self.var_test.get()),
            post_checksum=bool(self.var_checksum.get()),
            post_delete=bool(self.var_delete.get()),
            output_mode=self.var_out_mode.get(),
            output_dir=self.var_out_dir.get(),
        )

    def _on_preset_select(self, _evt=None) -> None:
        idx = self.preset_combo.current()
        if idx >= 0:
            self._load_preset(idx)

    def _save_preset(self) -> None:
        if not self._presets:
            return
        preset = self._current_preset_from_ui()
        self._presets[self._current_preset_idx] = preset
        _save_presets(self.config_obj, self._presets)
        self.status.configure(text=f"Preset '{preset.name}' saved.")

    def _new_preset(self) -> None:
        name = _ask_string(self, "New Preset", "Preset name:")
        if not name:
            return
        preset = self._current_preset_from_ui(name)
        self._presets.append(preset)
        self._current_preset_idx = len(self._presets) - 1
        _save_presets(self.config_obj, self._presets)
        self._refresh_preset_combo()
        self.preset_combo.current(self._current_preset_idx)

    def _delete_preset(self) -> None:
        if not self._presets:
            return
        name = self._presets[self._current_preset_idx].name
        if not messagebox.askyesno("Delete Preset",
                                   f"Delete preset '{name}'?", parent=self):
            return
        self._presets.pop(self._current_preset_idx)
        self._current_preset_idx = max(0, self._current_preset_idx - 1)
        _save_presets(self.config_obj, self._presets)
        self._refresh_preset_combo()
        if self._presets:
            self._load_preset(self._current_preset_idx)

    # ── UI state helpers ──────────────────────────────────────────────────────

    def _on_fmt_change(self) -> None:
        fmt = self.var_fmt.get()
        tab_map = {"FLAC": 0, "WAV": 1, "AIFF": 1, "MP3": 2, "AAC": 3, "OGG": 4}
        self.q_nb.select(tab_map.get(fmt, 0))

    def _on_mp3_mode(self) -> None:
        pass  # quality widgets always visible; mode label suffices

    def _on_close(self) -> None:
        if self._running and not messagebox.askyesno(
                "Batch Converter",
                "Conversion in progress. Close anyway?", parent=self):
            return
        self.destroy()

    # ── Conversion ────────────────────────────────────────────────────────────

    def _start(self) -> None:
        if not self._files:
            return
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror("Batch Converter",
                                 "ffmpeg not found. Install via the app installer or "
                                 "Tools → Update all CLI tools.")
            return
        preset = self._current_preset_from_ui()
        out_mode = self.var_out_mode.get()
        out_dir  = Path(self.var_out_dir.get()) if out_mode == "custom" else None
        if out_mode == "custom" and not self.var_out_dir.get():
            messagebox.showerror("Batch Converter",
                                 "Please set a custom output folder.")
            return

        self._running = True
        self.btn_convert.configure(state="disabled", text="Converting…")
        self.progress.configure(maximum=len(self._files), value=0)
        # Reset row statuses
        for iid in self.tv.get_children():
            self.tv.item(iid, values=(
                "·", *list(self.tv.item(iid, "values"))[1:]),
                tags=())

        threading.Thread(
            target=self._worker,
            args=(ffmpeg, list(self._files), preset, out_dir),
            daemon=True,
        ).start()

    def _worker(self, ffmpeg: Path, files: list[Path],
                preset: ConversionPreset, out_dir: Optional[Path]) -> None:
        ok = errors = skipped = 0
        ext = OUTPUT_EXT.get(preset.fmt, ".flac")
        children = self.tv.get_children()

        for i, src in enumerate(files):
            # Determine output path
            if out_dir:
                dst_dir = out_dir
                if self.var_keep_struct.get() and src.parent != src.parent.parent:
                    dst_dir = out_dir / src.parent.name
                dst_dir.mkdir(parents=True, exist_ok=True)
            else:
                dst_dir = src.parent
            dst = dst_dir / (src.stem + ext)

            self.after(0, lambda iid=children[i]: self.tv.item(
                iid, values=("⟳", *list(self.tv.item(iid, "values"))[1:]),
                tags=("info",)))
            self.after(0, lambda lbl=f"[{i+1}/{len(files)}] {src.name}":
                       self.status.configure(text=lbl))

            # Skip check
            if self.var_skip_exists.get() and dst.exists():
                self.after(0, lambda iid=children[i]: self.tv.item(
                    iid, values=("—", *list(self.tv.item(iid, "values"))[1:]),
                    tags=("info",)))
                skipped += 1
                self.after(0, lambda v=i+1: self.progress.configure(value=v))
                continue

            # Convert
            cmd = _build_cmd(ffmpeg, src, dst, preset)
            r = subprocess.run(cmd, capture_output=True, timeout=600,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                err_msg = (r.stderr or b"").decode("utf-8", errors="replace")[-120:]
                errors += 1
                self.after(0, lambda iid=children[i], e=err_msg: self.tv.item(
                    iid, values=("✗", *list(self.tv.item(iid, "values"))[1:]),
                    tags=("err",)))
                self.after(0, lambda v=i+1: self.progress.configure(value=v))
                continue

            # Test
            if preset.post_test:
                tr = subprocess.run(_test_cmd(ffmpeg, dst), capture_output=True,
                                    timeout=120,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if tr.returncode != 0:
                    errors += 1
                    self.after(0, lambda iid=children[i]: self.tv.item(
                        iid, values=("✗", *list(self.tv.item(iid, "values"))[1:]),
                        tags=("err",)))
                    self.after(0, lambda v=i+1: self.progress.configure(value=v))
                    continue

            # Delete source
            if preset.post_delete:
                try:
                    src.unlink()
                except OSError:
                    pass

            ok += 1
            self.after(0, lambda iid=children[i]: self.tv.item(
                iid, values=("✓", *list(self.tv.item(iid, "values"))[1:]),
                tags=("ok",)))
            self.after(0, lambda v=i+1: self.progress.configure(value=v))

        # Checksum
        if preset.post_checksum and ok > 0:
            self._write_checksums(files, preset, out_dir)

        self.after(0, lambda: self._done(ok, errors, skipped))

    def _write_checksums(self, files: list[Path], preset: ConversionPreset,
                         out_dir: Optional[Path]) -> None:
        import hashlib
        ext = OUTPUT_EXT.get(preset.fmt, ".flac")
        groups: dict[Path, list[Path]] = {}
        for src in files:
            dst_dir = out_dir or src.parent
            dst = dst_dir / (src.stem + ext)
            if dst.exists():
                groups.setdefault(dst.parent, []).append(dst)
        for folder, paths in groups.items():
            md5_path = folder / f"{folder.name}.md5"
            lines: list[str] = []
            for p in sorted(paths):
                try:
                    h = hashlib.md5(p.read_bytes()).hexdigest()
                    lines.append(f"{h}  {p.name}")
                except OSError:
                    pass
            if lines:
                try:
                    md5_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                except OSError:
                    pass

    def _done(self, ok: int, errors: int, skipped: int) -> None:
        self._running = False
        self.btn_convert.configure(state="normal", text="Convert All")
        parts = [f"✓ {ok} converted"]
        if skipped:
            parts.append(f"— {skipped} skipped")
        if errors:
            parts.append(f"✗ {errors} failed")
        self.status.configure(text="   ".join(parts))
        if errors:
            messagebox.showerror("Batch Converter",
                                 f"{ok} converted, {errors} failed.\n"
                                 "Check the file list for ✗ markers.")
        else:
            messagebox.showinfo("Batch Converter",
                                f"Done. {ok} file(s) converted successfully.")


# ── Helper: simple string input dialog ───────────────────────────────────────

def _ask_string(parent, title: str, prompt: str) -> Optional[str]:
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent)
    dlg.grab_set()
    dlg.resizable(False, False)
    _t.apply(dlg)
    ttk.Label(dlg, text=prompt, padding=(12, 10, 12, 4)).pack()
    var = tk.StringVar()
    entry = ttk.Entry(dlg, textvariable=var, width=36)
    entry.pack(padx=12)
    entry.focus_set()
    result: list[Optional[str]] = [None]
    bar = ttk.Frame(dlg, padding=(12, 8))
    bar.pack(fill="x")
    def _ok():
        result[0] = var.get().strip()
        dlg.destroy()
    ttk.Button(bar, text="Cancel", command=dlg.destroy).pack(side="right")
    ttk.Button(bar, text="OK", command=_ok).pack(side="right", padx=4)
    entry.bind("<Return>", lambda _e: _ok())
    entry.bind("<Escape>", lambda _e: dlg.destroy())
    parent.wait_window(dlg)
    return result[0]
