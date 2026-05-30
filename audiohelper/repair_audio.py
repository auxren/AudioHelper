"""Repair FLAC and WAV audio files.

Two repair modes
----------------
Header repair / remux (fast)
    Rewrites the container without touching the audio stream.
    ffmpeg -i broken.ext -c copy fixed.ext

    For WAV files whose header is so broken that ffmpeg cannot parse them,
    enable "Force raw format" and supply sample rate, channel count, and bit
    depth — ffmpeg will treat the body as raw PCM and write a fresh header.
    ffmpeg -f s{bits}le -ar SR -ac CH -i raw.wav -c copy fixed.wav

Full re-encode (slow)
    Decodes the audio data and re-encodes from scratch.  Fixes stream-level
    corruption as well as header issues.  Output format is chosen per file
    (FLAC stays FLAC, WAV stays WAV).

    FLAC: ffmpeg -i broken.flac -c:a flac -compression_level N fixed.flac
    WAV:  ffmpeg -i broken.wav  -c:a pcm_{bits}le                fixed.wav
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool

REPAIR_EXTS = {".wav", ".bwf", ".flac"}
_RAW_FMT = {"16": "s16le", "24": "s24le", "32": "s32le"}
_PCM_CODEC = {"16": "pcm_s16le", "24": "pcm_s24le", "32": "pcm_s32le"}


class RepairAudioDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Repair FLAC / WAV audio files")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("780x620")
        self.minsize(640, 480)

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ── file list ────────────────────────────────────────────────
        ttk.Label(frm, text="FLAC / WAV / BWF files to repair:").grid(
            row=0, column=0, sticky="w")
        lf = ttk.Frame(frm)
        lf.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=4)
        self.listbox = tk.Listbox(lf, selectmode="extended", activestyle="dotbox")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        br = ttk.Frame(frm)
        br.grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Button(br, text="Add files…",  command=self._add_files).pack(side="left")
        ttk.Button(br, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(br, text="Remove",      command=self._remove).pack(side="left")
        ttk.Button(br, text="Clear",
                   command=lambda: self.listbox.delete(0, "end")).pack(side="left", padx=4)

        # ── repair mode ───────────────────────────────────────────────
        mf = ttk.LabelFrame(frm, text="Repair mode", padding=8)
        mf.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.var_mode = tk.StringVar(value="remux")
        ttk.Radiobutton(
            mf,
            text="Header repair / remux  (fast — rewrites container without decoding audio)",
            variable=self.var_mode, value="remux",
            command=self._refresh_panels,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            mf,
            text="Full re-encode  (slow — decodes then re-encodes; also fixes stream-level corruption)",
            variable=self.var_mode, value="reencode",
            command=self._refresh_panels,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # ── force raw format (for WAV with completely broken header) ──
        self.ov_frame = ttk.LabelFrame(
            frm, text="Force raw format override  (use when remux fails — WAV only)",
            padding=8)
        self.ov_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.var_force = tk.BooleanVar(value=False)
        ttk.Checkbutton(self.ov_frame,
                        text="Treat file body as raw PCM (ignores broken header completely)",
                        variable=self.var_force,
                        command=self._refresh_panels).grid(
                            row=0, column=0, columnspan=8, sticky="w")
        ttk.Label(self.ov_frame, text="Sample rate:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.var_sr = tk.StringVar(value="44100")
        self.cb_sr = ttk.Combobox(self.ov_frame, textvariable=self.var_sr, width=9,
                                   state="readonly",
                                   values=["44100", "48000", "88200", "96000",
                                           "176400", "192000"])
        self.cb_sr.grid(row=1, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(self.ov_frame, text="Channels:").grid(
            row=1, column=2, sticky="w", padx=(12, 0), pady=(6, 0))
        self.var_ch = tk.StringVar(value="2")
        self.cb_ch = ttk.Combobox(self.ov_frame, textvariable=self.var_ch, width=4,
                                   state="readonly", values=["1", "2", "4", "6", "8"])
        self.cb_ch.grid(row=1, column=3, sticky="w", padx=6, pady=(6, 0))
        ttk.Label(self.ov_frame, text="Bit depth:").grid(
            row=1, column=4, sticky="w", padx=(12, 0), pady=(6, 0))
        self.var_bits_raw = tk.StringVar(value="16")
        self.cb_bits = ttk.Combobox(self.ov_frame, textvariable=self.var_bits_raw,
                                     width=5, state="readonly",
                                     values=["16", "24", "32"])
        self.cb_bits.grid(row=1, column=5, sticky="w", padx=6, pady=(6, 0))

        # ── re-encode quality options ─────────────────────────────────
        self.re_frame = ttk.LabelFrame(frm, text="Re-encode options", padding=8)
        self.re_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(self.re_frame, text="FLAC compression:").grid(row=0, column=0, sticky="w")
        self.var_flac_level = tk.IntVar(value=int(config.get("flac_compression_level", 8)))
        ttk.Combobox(self.re_frame, textvariable=self.var_flac_level, width=5,
                     state="readonly", values=list(range(0, 9))).grid(
                         row=0, column=1, sticky="w", padx=6)
        ttk.Label(self.re_frame, text="Output bit depth:").grid(
            row=0, column=2, sticky="w", padx=(20, 0))
        self.var_bits_out = tk.StringVar(value="Auto")
        ttk.Combobox(self.re_frame, textvariable=self.var_bits_out, width=8,
                     state="readonly", values=["Auto", "16", "24", "32"]).grid(
                         row=0, column=3, sticky="w", padx=6)
        ttk.Label(self.re_frame,
                  text="Auto preserves source bit depth",
                  foreground="gray").grid(row=0, column=4, sticky="w", padx=8)

        # ── output ────────────────────────────────────────────────────
        of = ttk.LabelFrame(frm, text="Output", padding=8)
        of.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.var_suffix = tk.StringVar(value="_repaired")
        ttk.Label(of, text="Output filename suffix:").grid(row=0, column=0, sticky="w")
        ttk.Entry(of, textvariable=self.var_suffix, width=16).grid(
            row=0, column=1, sticky="w", padx=6)
        ttk.Label(of, text='(empty = overwrite original)',
                  foreground="gray").grid(row=0, column=2, sticky="w", padx=4)
        ttk.Label(of, text="Output folder:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.var_outdir = tk.StringVar()
        self.entry_outdir = ttk.Entry(of, textvariable=self.var_outdir, width=48)
        self.entry_outdir.grid(row=1, column=1, columnspan=2, sticky="ew",
                               padx=6, pady=(4, 0))
        ttk.Button(of, text="…", width=3, command=self._pick_outdir).grid(
            row=1, column=3, pady=(4, 0))
        ttk.Label(of, text="(blank = same folder as source)",
                  foreground="gray").grid(row=2, column=1, columnspan=3,
                                          sticky="w", padx=6)
        of.columnconfigure(1, weight=1)

        # ── action buttons ────────────────────────────────────────────
        act = ttk.Frame(frm)
        act.grid(row=7, column=0, columnspan=4, sticky="e", pady=(10, 0))
        ttk.Button(act, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(act, text="Repair", command=self._start).pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        self._refresh_panels()

        if initial_files:
            for f in initial_files:
                if Path(f).suffix.lower() in REPAIR_EXTS:
                    self.listbox.insert("end", f)

    # ── UI helpers ────────────────────────────────────────────────────

    def _refresh_panels(self) -> None:
        mode  = self.var_mode.get()
        force = self.var_force.get()
        # Force-override controls only active in remux mode + force checkbox on
        for w in (self.cb_sr, self.cb_ch, self.cb_bits):
            w.configure(state="readonly" if (mode == "remux" and force) else "disabled")
        # Re-encode options only active in reencode mode
        for child in self.re_frame.winfo_children():
            if isinstance(child, (ttk.Combobox, ttk.Entry)):
                child.configure(state="readonly" if mode == "reencode" else "disabled")

    def _add_files(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Select FLAC / WAV files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("FLAC / WAV", ("*.flac", "*.wav", "*.bwf")),
                ("All files", "*.*"),
            ],
        )
        for f in files:
            self.listbox.insert("end", f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()

    def _add_folder(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and p.suffix.lower() in REPAIR_EXTS:
                self.listbox.insert("end", str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()

    def _remove(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    def _pick_outdir(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Output folder",
            initialdir=self.var_outdir.get() or None,
        )
        if d:
            self.var_outdir.set(d)

    # ── repair logic ─────────────────────────────────────────────────

    def _start(self) -> None:
        files = list(self.listbox.get(0, "end"))
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some files first.")
            return

        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                f"ffmpeg not found at:\n{ffmpeg}\n\n"
                "Install via Tools → Update all CLI tools.",
            )
            return

        mode    = self.var_mode.get()
        suffix  = self.var_suffix.get().strip()
        outdir  = Path(self.var_outdir.get().strip()) if self.var_outdir.get().strip() else None
        force   = self.var_force.get() and mode == "remux"

        if outdir:
            outdir.mkdir(parents=True, exist_ok=True)

        jobs: list[list[str]] = []
        for f in files:
            src  = Path(f)
            ext  = src.suffix.lower()
            stem = src.stem + (suffix or "")
            dest_dir = outdir if outdir else src.parent
            out  = dest_dir / (stem + ext)

            # Prevent silent overwrite when suffix is empty
            if not suffix and out.resolve(strict=False) == src.resolve(strict=False):
                messagebox.showerror(
                    "Trader's Little Jedi",
                    f"Output would overwrite source and suffix is empty.\n\n"
                    f"Set a suffix (e.g. _repaired) or choose a different folder.\n\n"
                    f"File: {src.name}",
                )
                return

            args = self._build_args(ffmpeg, src, out, mode, force)
            if args:
                jobs.append(args)

        if not jobs:
            return

        if not self.runner.run_sequence(jobs):
            messagebox.showwarning("Trader's Little Jedi", "Another job is already running.")
            return
        self.destroy()

    def _build_args(self, ffmpeg: Path, src: Path, out: Path,
                    mode: str, force: bool) -> list[str]:
        ext  = src.suffix.lower()
        args: list[str] = [str(ffmpeg), "-y", "-hide_banner"]

        if mode == "remux":
            if force and ext in {".wav", ".bwf"}:
                # Treat body as raw PCM — ignores the broken header
                bits = self.var_bits_raw.get()
                sr   = self.var_sr.get()
                ch   = self.var_ch.get()
                raw_fmt = _RAW_FMT.get(bits, "s16le")
                args += ["-f", raw_fmt, "-ar", sr, "-ac", ch]
            args += ["-i", str(src), "-c", "copy", str(out)]

        else:  # reencode
            bits_out = self.var_bits_out.get()
            level    = int(self.var_flac_level.get())
            args += ["-i", str(src)]

            if ext == ".flac":
                codec_args = ["-c:a", "flac", "-compression_level", str(level)]
                if bits_out == "16":
                    codec_args += ["-sample_fmt", "s16"]
                elif bits_out == "24":
                    codec_args += ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"]
                elif bits_out == "32":
                    codec_args += ["-sample_fmt", "s32", "-bits_per_raw_sample", "32"]
                args += codec_args
            elif ext in {".wav", ".bwf"}:
                if bits_out in _PCM_CODEC:
                    args += ["-c:a", _PCM_CODEC[bits_out]]
                else:
                    args += ["-c:a", "pcm_s24le"]  # Auto: safe default preserves 24-bit sources
            args.append(str(out))

        return args
