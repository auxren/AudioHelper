"""Convert encoding format — universal ffmpeg-driven audio converter.

Any source ffmpeg can read (WAV, BWF, FLAC, APE, SHN, DFF, DSF, MP3, AAC, OGG,
Opus, WMA, AIFF, WV, TTA, M4A) -> FLAC / WAV / MP3 / AAC / OGG Vorbis / Opus."""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool


TARGET_FORMATS = ("FLAC", "WAV", "MP3", "AAC", "OGG Vorbis", "Opus")
FORMAT_EXT = {
    "FLAC": ".flac",
    "WAV": ".wav",
    "MP3": ".mp3",
    "AAC": ".m4a",
    "OGG Vorbis": ".ogg",
    "Opus": ".opus",
}
# Containers that can carry an attached_pic (embedded cover art) stream
FORMATS_WITH_PICTURE = {"FLAC", "MP3", "AAC", "OGG Vorbis", "Opus"}
DSD_EXTS = {".dff", ".dsf"}


class ConvertDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Convert encoding format")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("760x700")

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ----- file list -----
        ttk.Label(frm, text="Files to convert:").grid(row=0, column=0, sticky="w")
        list_frame = ttk.Frame(frm)
        list_frame.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=4)
        self.listbox = tk.Listbox(list_frame, selectmode="extended", activestyle="dotbox")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Button(btn_row, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(btn_row, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(btn_row, text="Clear",
                   command=lambda: self.listbox.delete(0, "end")).pack(side="left", padx=4)

        # ----- output format radios -----
        fmt_frame = ttk.LabelFrame(frm, text="Output format", padding=8)
        fmt_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.var_format = tk.StringVar(value="FLAC")
        for i, name in enumerate(TARGET_FORMATS):
            ttk.Radiobutton(fmt_frame, text=name, variable=self.var_format, value=name,
                            command=self._refresh_format_panel).grid(row=0, column=i, padx=6, sticky="w")

        # ----- per-format option panel -----
        self.format_panel = ttk.LabelFrame(frm, text="Format options", padding=8)
        self.format_panel.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self._format_panels: dict[str, ttk.Frame] = {}
        self._build_flac_panel()
        self._build_wav_panel()
        self._build_mp3_panel()
        self._build_aac_panel()
        self._build_ogg_panel()
        self._build_opus_panel()
        self._refresh_format_panel()

        # ----- output / source -----
        out_frame = ttk.LabelFrame(frm, text="Output", padding=8)
        out_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.var_in_place = tk.BooleanVar(value=True)
        ttk.Checkbutton(out_frame, text="Write next to source files",
                        variable=self.var_in_place,
                        command=self._toggle_outdir).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(out_frame, text="Output directory:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_outdir = tk.StringVar(value=str(self.config_obj.get("last_output_dir", "")))
        self.entry_outdir = ttk.Entry(out_frame, textvariable=self.var_outdir, width=50)
        self.entry_outdir.grid(row=1, column=1, sticky="ew", padx=6)
        self.btn_outdir = ttk.Button(out_frame, text="…", width=3, command=self._pick_outdir)
        self.btn_outdir.grid(row=1, column=2, sticky="w")
        self._toggle_outdir()
        self.var_delete_source = tk.BooleanVar(value=False)
        ttk.Checkbutton(out_frame, text="Delete source after successful encode",
                        variable=self.var_delete_source).grid(row=2, column=0, columnspan=3, sticky="w", pady=2)
        out_frame.columnconfigure(1, weight=1)

        # ----- actions -----
        act = ttk.Frame(frm)
        act.grid(row=6, column=0, columnspan=4, sticky="e", pady=(10, 0))
        ttk.Button(act, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(act, text="Start", command=self._start).pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        if initial_files:
            from .action_picker import AUDIO_EXTS
            for f in initial_files:
                p = Path(f)
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                    self.listbox.insert("end", f)

    # ----- format panels -----

    def _build_flac_panel(self) -> None:
        f = ttk.Frame(self.format_panel)
        ttk.Label(f, text="Compression level:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_flac_level = tk.IntVar(value=int(self.config_obj.get("flac_compression_level", 8)))
        ttk.Combobox(f, textvariable=self.var_flac_level, width=5, state="readonly",
                     values=list(range(0, 9))).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(f, text="Sample rate (Hz):").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.var_flac_sr = tk.StringVar(value="Auto")
        ttk.Combobox(f, textvariable=self.var_flac_sr, width=10, state="readonly",
                     values=["Auto", "44100", "48000", "88200", "96000",
                             "176400", "192000"]).grid(row=0, column=3, sticky="w", padx=6)
        ttk.Label(f, text="Bit depth:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_flac_bits = tk.StringVar(value="Auto")
        ttk.Combobox(f, textvariable=self.var_flac_bits, width=8, state="readonly",
                     values=["Auto", "16", "24", "32"]).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(f, text="(DSD inputs default to 24-bit / 96 kHz)",
                  foreground="gray").grid(row=1, column=2, columnspan=2, sticky="w", padx=(12, 0))
        self._format_panels["FLAC"] = f

    def _build_wav_panel(self) -> None:
        f = ttk.Frame(self.format_panel)
        ttk.Label(f, text="Sample rate (Hz):").grid(row=0, column=0, sticky="w", pady=2)
        self.var_wav_sr = tk.StringVar(value="Auto")
        ttk.Combobox(f, textvariable=self.var_wav_sr, width=10, state="readonly",
                     values=["Auto", "44100", "48000", "88200", "96000",
                             "176400", "192000"]).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(f, text="Bit depth:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.var_wav_bits = tk.StringVar(value="Auto")
        ttk.Combobox(f, textvariable=self.var_wav_bits, width=8, state="readonly",
                     values=["Auto", "16", "24", "32"]).grid(row=0, column=3, sticky="w", padx=6)
        self._format_panels["WAV"] = f

    def _build_mp3_panel(self) -> None:
        f = ttk.Frame(self.format_panel)
        ttk.Label(f, text="Mode:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_mp3_mode = tk.StringVar(value=str(self.config_obj.get("mp3_mode", "VBR (variable bitrate)")))
        ttk.Combobox(f, textvariable=self.var_mp3_mode, width=22, state="readonly",
                     values=["CBR (constant bitrate)", "ABR (average bitrate)",
                             "VBR (variable bitrate)"]).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(f, text="CBR bitrate (kbit/s):").grid(row=1, column=0, sticky="w", pady=2)
        self.var_mp3_cbr = tk.IntVar(value=int(self.config_obj.get("lame_cbr_bitrate", 320)))
        ttk.Combobox(f, textvariable=self.var_mp3_cbr, width=6, state="readonly",
                     values=[96, 128, 160, 192, 224, 256, 320]).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(f, text="ABR bitrate (kbit/s):").grid(row=2, column=0, sticky="w", pady=2)
        self.var_mp3_abr = tk.IntVar(value=int(self.config_obj.get("lame_abr_bitrate", 192)))
        ttk.Spinbox(f, from_=64, to=320, increment=16,
                    textvariable=self.var_mp3_abr, width=6).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(f, text="VBR quality (0 best … 9 worst):").grid(row=3, column=0, sticky="w", pady=2)
        self.var_mp3_vbr = tk.IntVar(value=int(self.config_obj.get("lame_vbr_quality", 2)))
        ttk.Combobox(f, textvariable=self.var_mp3_vbr, width=5, state="readonly",
                     values=list(range(0, 10))).grid(row=3, column=1, sticky="w", padx=6)
        self._format_panels["MP3"] = f

    def _build_aac_panel(self) -> None:
        f = ttk.Frame(self.format_panel)
        ttk.Label(f, text="Bitrate (kbit/s):").grid(row=0, column=0, sticky="w", pady=2)
        self.var_aac_br = tk.IntVar(value=192)
        ttk.Combobox(f, textvariable=self.var_aac_br, width=6, state="readonly",
                     values=[96, 128, 160, 192, 224, 256, 320]).grid(row=0, column=1, sticky="w", padx=6)
        self._format_panels["AAC"] = f

    def _build_ogg_panel(self) -> None:
        f = ttk.Frame(self.format_panel)
        ttk.Label(f, text="Quality (-1 lowest … 10 highest):").grid(row=0, column=0, sticky="w", pady=2)
        self.var_ogg_q = tk.IntVar(value=6)
        ttk.Spinbox(f, from_=-1, to=10, increment=1,
                    textvariable=self.var_ogg_q, width=6).grid(row=0, column=1, sticky="w", padx=6)
        self._format_panels["OGG Vorbis"] = f

    def _build_opus_panel(self) -> None:
        f = ttk.Frame(self.format_panel)
        ttk.Label(f, text="Bitrate (kbit/s):").grid(row=0, column=0, sticky="w", pady=2)
        self.var_opus_br = tk.IntVar(value=128)
        ttk.Combobox(f, textvariable=self.var_opus_br, width=6, state="readonly",
                     values=[48, 64, 96, 128, 160, 192, 256, 320]).grid(row=0, column=1, sticky="w", padx=6)
        self._format_panels["Opus"] = f

    def _refresh_format_panel(self) -> None:
        for w in self.format_panel.winfo_children():
            w.pack_forget()
        panel = self._format_panels.get(self.var_format.get())
        if panel:
            panel.pack(fill="x")

    # ----- file list helpers -----

    def _add_files(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio files", ("*.wav", "*.bwf", "*.flac", "*.mp3", "*.ape", "*.shn",
                                 "*.dff", "*.dsf", "*.aiff", "*.aif", "*.wv", "*.tta",
                                 "*.m4a", "*.aac", "*.ogg", "*.opus", "*.wma")),
                ("All files", "*.*"),
            ],
        )
        for f in files:
            self.listbox.insert("end", f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()

    def _add_folder(self):
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        from .action_picker import AUDIO_EXTS
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                self.listbox.insert("end", str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    def _toggle_outdir(self):
        state = "disabled" if self.var_in_place.get() else "normal"
        self.entry_outdir.configure(state=state)
        self.btn_outdir.configure(state=state)

    def _pick_outdir(self):
        d = filedialog.askdirectory(
            parent=self, title="Output directory",
            initialdir=self.var_outdir.get() or None,
        )
        if d:
            self.var_outdir.set(d)

    # ----- start -----

    def _start(self):
        files = list(self.listbox.get(0, "end"))
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some files first.")
            return

        ffmpeg = get_tool("ffmpeg")
        exe = ffmpeg.path(self.config_obj)
        if not exe.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                f"ffmpeg.exe not found at:\n{exe}\n\n"
                "Open Tools → Update all CLI tools to install.",
            )
            return

        fmt = self.var_format.get()
        out_dir: Path | None = None
        if not self.var_in_place.get():
            d = self.var_outdir.get().strip()
            if not d:
                messagebox.showwarning(
                    "Trader's Little Jedi",
                    "Choose an output directory or check 'Write next to source files'.")
                return
            out_dir = Path(d)
            out_dir.mkdir(parents=True, exist_ok=True)
            self.config_obj["last_output_dir"] = str(out_dir)
            self.config_obj.save()

        has_dsd = any(Path(f).suffix.lower() in DSD_EXTS for f in files)
        codec_args = self._codec_args_for(fmt, has_dsd)

        delete_src = self.var_delete_source.get()
        ext = FORMAT_EXT[fmt]
        jobs: list = []
        for f in files:
            src = Path(f)
            out = (out_dir / (src.stem + ext)) if out_dir else src.with_suffix(ext)
            # Refuse silent input==output overwrite; rename instead
            try:
                same = out.resolve(strict=False) == src.resolve(strict=False)
            except OSError:
                same = False
            if same:
                out = out.with_name(out.stem + "-converted" + ext)
            # Preserve all tags + cover art where the target container allows it
            meta_args = ["-map", "0:a", "-map_metadata", "0"]
            if fmt in FORMATS_WITH_PICTURE:
                meta_args += ["-map", "0:v?", "-c:v", "copy"]
            args = [str(exe), "-y", "-hide_banner", "-i", str(src),
                    *meta_args, *codec_args, str(out)]
            jobs.append(args)
            if delete_src:
                jobs.append(_make_delete_job(src, out))

        if not self.runner.run_sequence(jobs):
            messagebox.showwarning("Trader's Little Jedi", "Another job is already running.")
            return
        self.destroy()

    # ----- codec args -----

    def _codec_args_for(self, fmt: str, has_dsd: bool) -> list[str]:
        if fmt == "FLAC":
            level = int(self.var_flac_level.get())
            sr = self.var_flac_sr.get()
            bits = self.var_flac_bits.get()
            if has_dsd:
                if sr == "Auto":
                    sr = "96000"
                if bits == "Auto":
                    bits = "24"
            args = ["-c:a", "flac", "-compression_level", str(level)]
            if sr != "Auto":
                args += ["-ar", sr]
            if bits == "16":
                args += ["-sample_fmt", "s16"]
            elif bits == "24":
                args += ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"]
            elif bits == "32":
                args += ["-sample_fmt", "s32", "-bits_per_raw_sample", "32"]
            self.config_obj["flac_compression_level"] = level
            self.config_obj.save()
            return args

        if fmt == "WAV":
            sr = self.var_wav_sr.get()
            bits = self.var_wav_bits.get()
            if has_dsd:
                if sr == "Auto":
                    sr = "96000"
                if bits == "Auto":
                    bits = "24"
            args: list[str] = []
            if bits == "16":
                args += ["-c:a", "pcm_s16le"]
            elif bits == "24":
                args += ["-c:a", "pcm_s24le"]
            elif bits == "32":
                args += ["-c:a", "pcm_s32le"]
            if sr != "Auto":
                args += ["-ar", sr]
            return args

        if fmt == "MP3":
            mode = self.var_mp3_mode.get()
            args = ["-c:a", "libmp3lame"]
            if mode.startswith("CBR"):
                args += ["-b:a", f"{int(self.var_mp3_cbr.get())}k"]
            elif mode.startswith("ABR"):
                args += ["-b:a", f"{int(self.var_mp3_abr.get())}k", "-abr", "1"]
            else:
                args += ["-q:a", str(int(self.var_mp3_vbr.get()))]
            self.config_obj["mp3_mode"] = mode
            self.config_obj["lame_cbr_bitrate"] = int(self.var_mp3_cbr.get())
            self.config_obj["lame_abr_bitrate"] = int(self.var_mp3_abr.get())
            self.config_obj["lame_vbr_quality"] = int(self.var_mp3_vbr.get())
            self.config_obj.save()
            return args

        if fmt == "AAC":
            return ["-c:a", "aac", "-b:a", f"{int(self.var_aac_br.get())}k"]
        if fmt == "OGG Vorbis":
            return ["-c:a", "libvorbis", "-q:a", str(int(self.var_ogg_q.get()))]
        if fmt == "Opus":
            return ["-c:a", "libopus", "-b:a", f"{int(self.var_opus_br.get())}k"]
        raise ValueError(f"unknown format: {fmt}")


def _make_delete_job(src: Path, out: Path):
    def delete_source():
        """delete source after verifying encoded output exists and is non-empty"""
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(
                f"encoded output missing/empty, refusing to delete source: {out}"
            )
        src.unlink(missing_ok=True)
    delete_source.__doc__ = f"delete source {src.name} (verified {out.name} exists)"
    return delete_source
