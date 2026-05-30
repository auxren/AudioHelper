import struct
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool

# WAV/BWF are natively understood by flac and lame CLIs.
# AIFF/AIF are natively decoded by flac CLI; ffmpeg handles AIFF→MP3.
WAV_LIKE_EXTS = {".wav", ".bwf", ".aiff", ".aif"}

# RIFF WAV format tags
_WAVE_FORMAT_PCM        = 0x0001  # integer PCM — flac CLI accepts this
_WAVE_FORMAT_IEEE_FLOAT = 0x0003  # 32/64-bit float — flac CLI rejects this
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE  # may contain float subformat


def _wav_is_float(path: Path) -> bool:
    """Return True if the WAV file uses IEEE float PCM (format type 3 or extensible float).

    The flac CLI rejects these with 'unsupported format type 3'.
    Reads only the first 68 bytes — safe for any WAV file.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(68)
        if len(data) < 22 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
            return False
        # Find fmt  chunk (may not start at offset 12 if there is a JUNK chunk)
        i = 12
        while i + 8 <= len(data):
            chunk_id   = data[i:i+4]
            chunk_size = struct.unpack_from("<I", data, i+4)[0]
            if chunk_id == b"fmt ":
                if i + 10 > len(data):
                    break
                fmt_tag = struct.unpack_from("<H", data, i+8)[0]
                if fmt_tag == _WAVE_FORMAT_IEEE_FLOAT:
                    return True
                if fmt_tag == _WAVE_FORMAT_EXTENSIBLE and i + 34 <= len(data):
                    # WAVEFORMATEXTENSIBLE: SubFormat GUID starts at body+24 (i+32)
                    # First 2 bytes of GUID = actual codec tag
                    sub_tag = struct.unpack_from("<H", data, i+32)[0]
                    return sub_tag == _WAVE_FORMAT_IEEE_FLOAT
                return False
            i += 8 + chunk_size + (chunk_size & 1)  # chunks are word-aligned
    except (OSError, struct.error):
        pass
    return False

FORMATS = ("FLAC", "MP3")


class EncodeWavDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Encode WAV files")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("720x560")

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ----- file list -----
        ttk.Label(frm, text="WAV / BWF / AIFF files to encode:").grid(row=0, column=0, sticky="w")
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
        ttk.Button(btn_row, text="Remove selected", command=self._remove_selected).pack(side="left")
        ttk.Button(btn_row, text="Clear", command=lambda: self.listbox.delete(0, "end")).pack(side="left", padx=4)

        # ----- output format choice -----
        fmt_frame = ttk.LabelFrame(frm, text="Output format", padding=8)
        fmt_frame.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.var_format = tk.StringVar(value="FLAC")
        for i, name in enumerate(FORMATS):
            ttk.Radiobutton(fmt_frame, text=name, variable=self.var_format, value=name,
                            command=self._refresh_format_panel).grid(row=0, column=i, padx=8, sticky="w")

        # ----- per-format options (swap based on choice) -----
        self.format_panel = ttk.LabelFrame(frm, text="Format options", padding=8)
        self.format_panel.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self._format_panels: dict[str, ttk.Frame] = {}
        self._build_flac_panel()
        self._build_mp3_panel()
        self._refresh_format_panel()

        # ----- output / source handling -----
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
        ttk.Checkbutton(out_frame,
                        text="Delete source file after successful encode",
                        variable=self.var_delete_source).grid(row=2, column=0, columnspan=3, sticky="w", pady=2)

        # ----- actions -----
        act = ttk.Frame(frm)
        act.grid(row=6, column=0, columnspan=4, sticky="e", pady=(10, 0))
        ttk.Button(act, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(act, text="Start", command=self._start).pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        if initial_files:
            for f in initial_files:
                if Path(f).suffix.lower() in WAV_LIKE_EXTS:
                    self.listbox.insert("end", f)

    # ----- per-format panels -----

    def _build_flac_panel(self) -> None:
        f = ttk.Frame(self.format_panel)
        ttk.Label(f, text="Compression level (0 fast … 8 best):").grid(row=0, column=0, sticky="w", pady=2)
        self.var_flac_level = tk.IntVar(value=int(self.config_obj.get("flac_compression_level", 8)))
        ttk.Combobox(f, textvariable=self.var_flac_level, width=5, state="readonly",
                     values=list(range(0, 9))).grid(row=0, column=1, sticky="w", padx=6)
        self.var_flac_verify = tk.BooleanVar(value=bool(self.config_obj.get("flac_verify", True)))
        ttk.Checkbutton(f, text="Verify decoded output matches input (--verify)",
                        variable=self.var_flac_verify).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        self._format_panels["FLAC"] = f

    def _build_mp3_panel(self) -> None:
        f = ttk.Frame(self.format_panel)
        ttk.Label(f, text="Mode:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_mp3_mode = tk.StringVar(value=str(self.config_obj.get("mp3_mode", "VBR (variable bitrate)")))
        ttk.Combobox(f, textvariable=self.var_mp3_mode, width=24, state="readonly",
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

    def _refresh_format_panel(self) -> None:
        for w in self.format_panel.winfo_children():
            w.pack_forget()
        panel = self._format_panels.get(self.var_format.get())
        if panel:
            panel.pack(fill="x")

    # ----- file list helpers -----

    def _add_files(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Select WAV / BWF / AIFF files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("WAV / BWF / AIFF", ("*.wav", "*.bwf", "*.aiff", "*.aif")),
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
        seen: set[str] = set()
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and p.suffix.lower() in WAV_LIKE_EXTS:
                s = str(p)
                if s not in seen:
                    seen.add(s)
                    self.listbox.insert("end", s)
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()

    def _remove_selected(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    def _toggle_outdir(self) -> None:
        state = "disabled" if self.var_in_place.get() else "normal"
        self.entry_outdir.configure(state=state)
        self.btn_outdir.configure(state=state)

    def _pick_outdir(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Output directory",
            initialdir=self.var_outdir.get() or None,
        )
        if d:
            self.var_outdir.set(d)

    # ----- encode -----

    def _start(self) -> None:
        files = list(self.listbox.get(0, "end"))
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some WAV / AIFF files first.")
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

        try:
            builder = self._builder_for(fmt)
        except FileNotFoundError as e:
            messagebox.showerror(
                "Trader's Little Jedi",
                f"{e}\n\nOpen Tools → Update all CLI tools to install or download the missing binary.",
            )
            return

        delete_src = self.var_delete_source.get()
        ext = _out_ext(fmt)
        jobs: list = []
        for f in files:
            src = Path(f)
            out = (out_dir / (src.stem + ext)) if out_dir else src.with_suffix(ext)
            jobs.append(builder(src, out))
            if delete_src:
                jobs.append(_make_delete_job(src, out))

        if not self.runner.run_sequence(jobs):
            messagebox.showwarning("Trader's Little Jedi", "Another job is already running.")
            return
        self.destroy()

    def _builder_for(self, fmt: str):
        if fmt == "FLAC":
            flac_exe   = get_tool("flac").path(self.config_obj)
            ffmpeg_exe = get_tool("ffmpeg").path(self.config_obj)
            if not flac_exe.exists():
                raise FileNotFoundError(f"flac.exe not found at: {flac_exe}")
            level  = int(self.var_flac_level.get())
            verify = bool(self.var_flac_verify.get())
            self.config_obj["flac_compression_level"] = level
            self.config_obj["flac_verify"] = verify
            self.config_obj.save()

            def build_flac(src: Path, out: Path):
                is_aiff      = src.suffix.lower() in (".aiff", ".aif")
                is_float_wav = (not is_aiff) and _wav_is_float(src)
                if is_aiff or is_float_wav:
                    # flac CLI can't handle AIFF or IEEE-float WAV (format type 3).
                    # Use ffmpeg which converts on the fly without a temp file.
                    if not ffmpeg_exe.exists():
                        raise FileNotFoundError(
                            f"ffmpeg.exe not found at: {ffmpeg_exe}\n"
                            "ffmpeg is required for AIFF and 32-bit float WAV → FLAC."
                        )
                    return [
                        str(ffmpeg_exe), "-y", "-hide_banner",
                        "-i", str(src),
                        "-c:a", "flac",
                        "-compression_level", str(level),
                        "-map_metadata", "0",
                        str(out),
                    ]
                # Standard integer PCM WAV/BWF — use the flac CLI directly
                args = [str(flac_exe), f"-{level}", "-f"]
                if verify:
                    args.append("--verify")
                args += ["-o", str(out), str(src)]
                return args
            return build_flac

        if fmt == "MP3":
            mode = self.var_mp3_mode.get()
            cbr  = int(self.var_mp3_cbr.get())
            abr  = int(self.var_mp3_abr.get())
            vbr  = int(self.var_mp3_vbr.get())
            self.config_obj["mp3_mode"] = mode
            self.config_obj["lame_cbr_bitrate"] = cbr
            self.config_obj["lame_abr_bitrate"] = abr
            self.config_obj["lame_vbr_quality"] = vbr
            self.config_obj.save()

            lame_exe  = get_tool("lame").path(self.config_obj)
            ffmpeg_exe = get_tool("ffmpeg").path(self.config_obj)

            def build_mp3(src: Path, out: Path):
                is_aiff = src.suffix.lower() in (".aiff", ".aif")
                if is_aiff:
                    # LAME doesn't read AIFF; route through ffmpeg
                    if not ffmpeg_exe.exists():
                        raise FileNotFoundError(f"ffmpeg.exe not found at: {ffmpeg_exe}")
                    args = [str(ffmpeg_exe), "-y", "-hide_banner", "-i", str(src),
                            "-c:a", "libmp3lame"]
                    if mode.startswith("CBR"):
                        args += ["-b:a", f"{cbr}k"]
                    elif mode.startswith("ABR"):
                        args += ["-b:a", f"{abr}k", "-abr", "1"]
                    else:
                        args += ["-q:a", str(vbr)]
                    args += ["-map_metadata", "0", str(out)]
                    return args
                # WAV/BWF: use LAME directly
                if not lame_exe.exists():
                    raise FileNotFoundError(f"lame.exe not found at: {lame_exe}")
                args = [str(lame_exe)]
                if mode.startswith("CBR"):
                    args += ["-b", str(cbr)]
                elif mode.startswith("ABR"):
                    args += ["--abr", str(abr)]
                else:
                    args += ["-V", str(vbr)]
                args += [str(src), str(out)]
                return args
            return build_mp3

        raise ValueError(f"unknown format: {fmt}")


def _out_ext(fmt: str) -> str:
    return {"FLAC": ".flac", "MP3": ".mp3"}[fmt]


def _make_delete_job(src: Path, out: Path):
    def delete_source():
        """delete source after verifying encoded output exists and is non-empty"""
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(f"encoded output missing/empty, refusing to delete source: {out}")
        src.unlink(missing_ok=True)
    delete_source.__doc__ = f"delete source {src.name} (verified {out.name} exists)"
    return delete_source
