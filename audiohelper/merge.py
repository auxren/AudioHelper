"""Gapless audio merge via ffmpeg's concat demuxer.

Supported input: WAV, BWF, FLAC, MP3, APE, SHN, AAC, M4A, OGG, WV, TTA,
                 AIFF, AIF, DFF (DSDIFF), DSF — anything ffmpeg can read.

Output formats
--------------
Keep input format   – concat demuxer + -c copy (fastest, lossless)
DFF / DSF           – concat + -c copy into DSD container (shown only when
                      at least one DFF or DSF file is in the list)
FLAC / WAV          – concat demuxer + decode → re-encode (sample rate and
                      bit depth configurable)
MP3                 – concat demuxer + decode → libmp3lame -q:a 0
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool

_DSD_EXTS = {".dff", ".dsf"}

_FMT_SAME = "Keep input format  (copy — fastest, lossless)"
_FMT_DFF  = "Output as DFF  (DSDIFF / copy)"
_FMT_FLAC = "Re-encode: FLAC"
_FMT_WAV  = "Re-encode: WAV"
_FMT_MP3  = "Re-encode: MP3  (lossy)"

_FMTS_BASE = [_FMT_SAME, _FMT_FLAC, _FMT_WAV, _FMT_MP3]
_FMTS_DSD  = [_FMT_SAME, _FMT_DFF, _FMT_FLAC, _FMT_WAV, _FMT_MP3]

_SR_VALUES   = [44100, 48000, 88200, 96000, 176400, 192000]
_BITS_VALUES = [16, 24, 32]


class MergeDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Merge audio files (gapless)")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("820x660")

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm,
                  text="Files to merge — order matters (↑ / ↓ to reorder):").grid(
            row=0, column=0, columnspan=4, sticky="w")

        lf = ttk.Frame(frm)
        lf.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=4)
        self.listbox = tk.Listbox(lf, selectmode="extended",
                                  activestyle="dotbox")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        br = ttk.Frame(frm)
        br.grid(row=2, column=0, columnspan=4, sticky="w", pady=4)
        ttk.Button(br, text="Add files…",
                   command=self._add_files).pack(side="left")
        ttk.Button(br, text="Add folder…",
                   command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(br, text="Remove",
                   command=self._remove).pack(side="left")
        ttk.Button(br, text="↑", width=3,
                   command=self._move_up).pack(side="left", padx=(10, 0))
        ttk.Button(br, text="↓", width=3,
                   command=self._move_down).pack(side="left")
        ttk.Button(br, text="Clear",
                   command=lambda: (self.listbox.delete(0, "end"),
                                    self._on_list_changed())).pack(
                                        side="left", padx=4)

        # ── output options ────────────────────────────────────────────
        opt = ttk.LabelFrame(frm, text="Output", padding=8)
        opt.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        ttk.Label(opt, text="Format:").grid(row=0, column=0, sticky="w")
        self.var_format = tk.StringVar(value=_FMT_SAME)
        self.fmt_cb = ttk.Combobox(opt, textvariable=self.var_format,
                                   state="readonly", width=44,
                                   values=_FMTS_BASE)
        self.fmt_cb.grid(row=0, column=1, columnspan=3, sticky="w", padx=6)
        self.fmt_cb.bind("<<ComboboxSelected>>",
                         lambda _: self._on_fmt_changed())

        # PCM options (relevant for FLAC and WAV output)
        self.lbl_sr = ttk.Label(opt, text="Sample rate (Hz):")
        self.lbl_sr.grid(row=1, column=0, sticky="w", pady=2)
        self.var_sr = tk.IntVar(value=96000)
        self.cb_sr = ttk.Combobox(opt, textvariable=self.var_sr,
                                  state="readonly", width=10,
                                  values=_SR_VALUES)
        self.cb_sr.grid(row=1, column=1, sticky="w", padx=6)
        self.lbl_bits = ttk.Label(opt, text="Bit depth:")
        self.lbl_bits.grid(row=1, column=2, sticky="w", padx=(12, 0))
        self.var_bits = tk.IntVar(value=24)
        self.cb_bits = ttk.Combobox(opt, textvariable=self.var_bits,
                                    state="readonly", width=6,
                                    values=_BITS_VALUES)
        self.cb_bits.grid(row=1, column=3, sticky="w", padx=6)

        ttk.Label(opt, text="Output file:").grid(row=2, column=0,
                                                  sticky="w", pady=4)
        self.var_outfile = tk.StringVar()
        ttk.Entry(opt, textvariable=self.var_outfile, width=50).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=6, pady=4)
        ttk.Button(opt, text="…", width=3,
                   command=self._pick_output).grid(row=2, column=3, sticky="w")
        opt.columnconfigure(1, weight=1)

        act = ttk.Frame(frm)
        act.grid(row=4, column=0, columnspan=4, sticky="e", pady=(10, 0))
        ttk.Button(act, text="Cancel",
                   command=self.destroy).pack(side="right")
        ttk.Button(act, text="Merge",
                   command=self._start).pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        self._on_fmt_changed()   # set initial PCM-row state

        if initial_files:
            from .action_picker import AUDIO_EXTS
            for f in initial_files:
                if Path(f).suffix.lower() in AUDIO_EXTS:
                    self.listbox.insert("end", f)
            self._on_list_changed()

    # ------------------------------------------------------------------ #
    # List management
    # ------------------------------------------------------------------ #

    def _add_files(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files to merge",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio files",
                 ("*.wav", "*.bwf", "*.flac", "*.mp3", "*.ape",
                  "*.dff", "*.dsf", "*.shn", "*.aiff", "*.aif",
                  "*.wv", "*.tta", "*.m4a", "*.aac", "*.ogg")),
                ("DSD files", ("*.dff", "*.dsf")),
                ("All files", "*.*"),
            ],
        )
        for f in files:
            self.listbox.insert("end", f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
            self._on_list_changed()

    def _add_folder(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        from .action_picker import AUDIO_EXTS
        seen: set[str] = set()
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                s = str(p)
                if s not in seen:
                    seen.add(s)
                    self.listbox.insert("end", s)
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        self._on_list_changed()

    def _remove(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
        self._on_list_changed()

    def _move_up(self) -> None:
        sel = list(self.listbox.curselection())
        if not sel or sel[0] == 0:
            return
        for i in sel:
            txt = self.listbox.get(i)
            self.listbox.delete(i)
            self.listbox.insert(i - 1, txt)
        self.listbox.selection_clear(0, "end")
        for i in sel:
            self.listbox.selection_set(i - 1)

    def _move_down(self) -> None:
        sel = list(self.listbox.curselection())
        if not sel or sel[-1] == self.listbox.size() - 1:
            return
        for i in reversed(sel):
            txt = self.listbox.get(i)
            self.listbox.delete(i)
            self.listbox.insert(i + 1, txt)
        self.listbox.selection_clear(0, "end")
        for i in sel:
            self.listbox.selection_set(i + 1)

    # ------------------------------------------------------------------ #
    # Format / UI state
    # ------------------------------------------------------------------ #

    def _on_list_changed(self) -> None:
        """Update format combobox options based on whether DSD files are listed."""
        items = list(self.listbox.get(0, "end"))
        has_dsd = any(Path(f).suffix.lower() in _DSD_EXTS for f in items)
        new_vals = _FMTS_DSD if has_dsd else _FMTS_BASE
        self.fmt_cb.configure(values=new_vals)
        if self.var_format.get() not in new_vals:
            # Default for DSD list: keep input format (DSD copy)
            self.var_format.set(_FMT_SAME)
        self._on_fmt_changed()
        self._suggest_output()

    def _on_fmt_changed(self) -> None:
        """Show / hide PCM settings row based on selected format."""
        fmt = self.var_format.get()
        pcm_active = fmt in (_FMT_FLAC, _FMT_WAV)
        state = "normal" if pcm_active else "disabled"
        for w in (self.lbl_sr, self.cb_sr, self.lbl_bits, self.cb_bits):
            w.configure(state=state)
        self._refresh_outfile_ext()

    # ------------------------------------------------------------------ #
    # Output filename helpers
    # ------------------------------------------------------------------ #

    def _out_ext(self) -> str:
        fmt = self.var_format.get()
        if fmt == _FMT_FLAC:  return ".flac"
        if fmt == _FMT_WAV:   return ".wav"
        if fmt == _FMT_MP3:   return ".mp3"
        if fmt == _FMT_DFF:   return ".dff"
        # SAME FORMAT — use first file's extension
        items = list(self.listbox.get(0, "end"))
        if items:
            return Path(items[0]).suffix.lower()
        return ".flac"

    def _suggest_output(self) -> None:
        if self.var_outfile.get():
            return
        if self.listbox.size() == 0:
            return
        first  = Path(self.listbox.get(0))
        base   = first.parent.name or first.stem
        ext    = self._out_ext()
        self.var_outfile.set(str(first.parent / f"{base}{ext}"))

    def _refresh_outfile_ext(self) -> None:
        cur = self.var_outfile.get()
        if not cur:
            self._suggest_output()
            return
        new_ext = self._out_ext()
        p = Path(cur)
        if p.suffix.lower() != new_ext:
            self.var_outfile.set(str(p.with_suffix(new_ext)))

    def _pick_output(self) -> None:
        cur       = self.var_outfile.get()
        init_dir  = (str(Path(cur).parent)
                     if cur else self.config_obj.get("last_output_dir") or None)
        init_name = Path(cur).name if cur else ""
        ext       = self._out_ext()
        f = filedialog.asksaveasfilename(
            parent=self, title="Save merged file as…",
            initialdir=init_dir, initialfile=init_name,
            defaultextension=ext,
            filetypes=[
                ("DSD (DSDIFF)",  "*.dff"),
                ("DSD (Sony)",    "*.dsf"),
                ("FLAC",          "*.flac"),
                ("WAV",           "*.wav"),
                ("MP3",           "*.mp3"),
                ("All files",     "*.*"),
            ],
        )
        if f:
            self.var_outfile.set(f)

    # ------------------------------------------------------------------ #
    # Merge
    # ------------------------------------------------------------------ #

    def _start(self) -> None:
        files = list(self.listbox.get(0, "end"))
        if len(files) < 2:
            messagebox.showwarning("Trader's Little Jedi",
                                   "Add at least 2 files to merge.")
            return

        out = self.var_outfile.get().strip()
        if not out:
            messagebox.showwarning("Trader's Little Jedi",
                                   "Choose an output file first.")
            return

        out_p = Path(out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_ext = out_p.suffix.lower()

        # Safety: warn if trying to overwrite a source file
        sources = {Path(f).resolve() for f in files}
        if out_p.resolve() in sources:
            messagebox.showerror("Trader's Little Jedi",
                                 "Output file must not be one of the input files.")
            return

        fmt = self.var_format.get()

        # ── DFF output: use pure-Python DSDIFF merger (ffmpeg has no DFF muxer) ──
        wants_dff = (fmt == _FMT_DFF or
                     (fmt == _FMT_SAME and out_ext == ".dff"))
        all_dff   = all(Path(f).suffix.lower() == ".dff" for f in files)

        if wants_dff:
            if not all_dff:
                messagebox.showerror(
                    "Trader's Little Jedi",
                    "DFF output currently requires all input files to be DFF.\n"
                    "To merge DSF files, use TASCAM Hi-Res Editor → Combine,\n"
                    "or Tools → Launch TASCAM Hi-Res Editor.",
                )
                return
            from .dff_merge import merge_dff
            in_paths = [Path(f) for f in files]
            out_path = out_p

            def _do_dff_merge():
                """Merging DFF files (pure Python, no re-encode)"""
                total_mb = sum(p.stat().st_size for p in in_paths) / 1_048_576
                self.runner.on_output(
                    f"  Input: {len(in_paths)} files  "
                    f"({total_mb:.1f} MiB total DSD data)\n"
                    f"  Output: {out_path}\n"
                )
                merge_dff(in_paths, out_path)
                out_mb = out_path.stat().st_size / 1_048_576
                self.runner.on_output(f"  Done — {out_mb:.1f} MiB written\n")

            if not self.runner.run_sequence([_do_dff_merge]):
                messagebox.showwarning("Trader's Little Jedi",
                                       "Another job is already running.")
                return
            self.destroy()
            return

        # ── All other formats: use ffmpeg concat demuxer ──────────────────────────
        exe = get_tool("ffmpeg").path(self.config_obj)
        if not exe.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                "ffmpeg.exe not found.\n"
                "Open Tools → Update all CLI tools to install it.",
            )
            return

        # Build ffmpeg concat list
        list_path = out_p.parent / f".audiohelper_merge_{os.getpid()}.txt"
        lines = []
        for f in files:
            esc = str(Path(f).resolve()).replace("\\", "/").replace("'", "''")
            lines.append(f"file '{esc}'\n")
        list_path.write_text("".join(lines), encoding="utf-8")

        if fmt == _FMT_SAME:
            codec_args = ["-c", "copy"]

        elif fmt == _FMT_FLAC:
            bits  = int(self.var_bits.get())
            level = self.config_obj.get("flac_compression_level", 8)
            if bits <= 16:
                codec_args = [
                    "-ar", str(self.var_sr.get()),
                    "-c:a", "flac",
                    "-sample_fmt", "s16",
                    "-compression_level", str(level),
                ]
            else:
                codec_args = [
                    "-ar", str(self.var_sr.get()),
                    "-c:a", "flac",
                    "-sample_fmt", "s32",
                    "-bits_per_raw_sample", str(bits),
                    "-compression_level", str(level),
                ]

        elif fmt == _FMT_WAV:
            bits = int(self.var_bits.get())
            pcm  = {16: "pcm_s16le",
                    24: "pcm_s24le",
                    32: "pcm_s32le"}.get(bits, "pcm_s24le")
            codec_args = [
                "-ar", str(self.var_sr.get()),
                "-c:a", pcm,
            ]

        else:  # MP3
            codec_args = ["-c:a", "libmp3lame", "-q:a", "0"]

        args = [
            str(exe), "-y", "-hide_banner",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-map_metadata", "0",
        ] + codec_args + [str(out_p)]

        lp = list_path
        def cleanup():
            try:
                lp.unlink()
            except FileNotFoundError:
                pass

        if not self.runner.run_sequence([args], finalize=cleanup):
            cleanup()
            messagebox.showwarning("Trader's Little Jedi",
                                   "Another job is already running.")
            return
        self.destroy()
