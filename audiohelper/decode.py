"""Decode audio files → WAV using ffmpeg.

Accepts any format ffmpeg can read (FLAC, APE, SHN, MP3, AAC, OGG, DFF, DSF,
WV, TTA, AIFF, etc.) and writes standard WAV at selectable bit depth.
DSD inputs (.dff/.dsf) default to 24-bit / 96 kHz output."""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool

DSD_EXTS = {".dff", ".dsf"}

WAV_FMTS: dict[str, str] = {
    "16-bit PCM": "pcm_s16le",
    "24-bit PCM": "pcm_s24le",
    "32-bit PCM": "pcm_s32le",
    "32-bit float": "pcm_f32le",
}

SAMPLE_RATES = ["Auto", "44100", "48000", "88200", "96000", "176400", "192000"]


class DecodeDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Decode audio files → WAV")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("700x560")

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ----- file list -----
        ttk.Label(frm, text="Files to decode:").grid(row=0, column=0, sticky="w")
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

        # ----- output format -----
        opt = ttk.LabelFrame(frm, text="WAV output options", padding=8)
        opt.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))

        ttk.Label(opt, text="Bit depth:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_fmt = tk.StringVar(value="16-bit PCM")
        ttk.Combobox(opt, textvariable=self.var_fmt, state="readonly", width=14,
                     values=list(WAV_FMTS.keys())).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(opt, text="DSD defaults to 24-bit / 96 kHz",
                  foreground="gray").grid(row=0, column=2, sticky="w", padx=12)

        ttk.Label(opt, text="Sample rate (Hz):").grid(row=1, column=0, sticky="w", pady=2)
        self.var_sr = tk.StringVar(value="Auto")
        ttk.Combobox(opt, textvariable=self.var_sr, state="readonly", width=10,
                     values=SAMPLE_RATES).grid(row=1, column=1, sticky="w", padx=6)

        # ----- output location -----
        out_frame = ttk.LabelFrame(frm, text="Output", padding=8)
        out_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.var_in_place = tk.BooleanVar(value=True)
        ttk.Checkbutton(out_frame, text="Write next to source files",
                        variable=self.var_in_place, command=self._toggle_outdir).grid(
                            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(out_frame, text="Output directory:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_outdir = tk.StringVar(value=str(self.config_obj.get("last_output_dir", "")))
        self.entry_outdir = ttk.Entry(out_frame, textvariable=self.var_outdir, width=50)
        self.entry_outdir.grid(row=1, column=1, sticky="ew", padx=6)
        self.btn_outdir = ttk.Button(out_frame, text="…", width=3, command=self._pick_outdir)
        self.btn_outdir.grid(row=1, column=2, sticky="w")
        self.var_del = tk.BooleanVar(value=False)
        ttk.Checkbutton(out_frame, text="Delete source after successful decode",
                        variable=self.var_del).grid(row=2, column=0, columnspan=3, sticky="w", pady=2)
        out_frame.columnconfigure(1, weight=1)
        self._toggle_outdir()

        act = ttk.Frame(frm)
        act.grid(row=5, column=0, columnspan=4, sticky="e", pady=(10, 0))
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

    # ----- helpers -----

    def _add_files(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio files", ("*.flac", "*.ape", "*.shn", "*.mp3", "*.aac",
                                 "*.m4a", "*.ogg", "*.opus", "*.wma", "*.dff",
                                 "*.dsf", "*.wv", "*.tta", "*.aiff", "*.aif",
                                 "*.wav", "*.bwf")),
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
        d = filedialog.askdirectory(parent=self, title="Output directory",
                                    initialdir=self.var_outdir.get() or None)
        if d:
            self.var_outdir.set(d)

    def _start(self):
        files = list(self.listbox.get(0, "end"))
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some files first.")
            return

        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                f"ffmpeg.exe not found at:\n{ffmpeg}\n\n"
                "Open Tools → Update all CLI tools to install.",
            )
            return

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

        codec = WAV_FMTS.get(self.var_fmt.get(), "pcm_s16le")
        sr = self.var_sr.get()
        delete_src = self.var_del.get()

        jobs: list = []
        for f in files:
            src = Path(f)
            is_dsd = src.suffix.lower() in DSD_EXTS
            dst = (out_dir / (src.stem + ".wav")) if out_dir else src.with_suffix(".wav")
            try:
                same = dst.resolve(strict=False) == src.resolve(strict=False)
            except OSError:
                same = False
            if same:
                dst = dst.with_name(dst.stem + "-decoded.wav")

            args: list[str] = [str(ffmpeg), "-y", "-hide_banner", "-i", str(src)]
            effective_sr = "96000" if (is_dsd and sr == "Auto") else (None if sr == "Auto" else sr)
            if effective_sr:
                args += ["-ar", effective_sr]
            effective_codec = "pcm_s24le" if (is_dsd and codec == "pcm_s16le") else codec
            args += ["-c:a", effective_codec]
            args.append(str(dst))
            jobs.append(args)

            if delete_src:
                jobs.append(_make_delete(src, dst))

        if not self.runner.run_sequence(jobs):
            messagebox.showwarning("Trader's Little Jedi", "Another job is already running.")
            return
        self.destroy()


def _make_delete(src: Path, out: Path):
    def _del():
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(f"output missing or empty — refusing to delete {src.name}")
        src.unlink(missing_ok=True)
    _del.__doc__ = f"delete source {src.name}"
    return _del
