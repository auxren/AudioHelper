"""DSD Edit / Trim dialog — Tascam Hi-Res Editor equivalent.

Loads a .dff or .dsf file, probes it with ffprobe, lets the user trim
start/end, adjust gain, then decodes to high-res PCM (FLAC or WAV).
DSD→DSD lossless copy is supported only when start=0 and no gain is applied.
"""

import json
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from .tools import get_tool
from .split import parse_time_str

DSD_EXTS = {".dff", ".dsf"}

_DSD_RATE_LABELS: dict[int, str] = {
    2822400:  "DSD64  (2.822 MHz)",
    5644800:  "DSD128 (5.645 MHz)",
    11289600: "DSD256 (11.29 MHz)",
    22579200: "DSD512 (22.58 MHz)",
}

_PCM_RATES = ["44100", "48000", "88200", "96000", "176400", "192000"]
_BIT_DEPTHS = ["16", "24", "32"]


class DsdEditDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("DSD Edit / Trim")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("680x630")

        self._duration: Optional[float] = None
        self._src_sample_rate: Optional[int] = None

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ----- source -----
        src_f = ttk.LabelFrame(frm, text="DSD source file (.dff or .dsf)", padding=8)
        src_f.pack(fill="x", pady=(0, 6))
        self.var_src = tk.StringVar()
        ttk.Entry(src_f, textvariable=self.var_src, width=52).grid(
            row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(src_f, text="…", width=3, command=self._pick_source).grid(row=0, column=1)
        ttk.Button(src_f, text="Load info", command=self._load_info).grid(
            row=0, column=2, padx=(6, 0))
        src_f.columnconfigure(0, weight=1)

        # ----- info -----
        info_f = ttk.LabelFrame(frm, text="File information", padding=8)
        info_f.pack(fill="x", pady=(0, 6))
        self.lbl_info = ttk.Label(info_f, text="(no file loaded)",
                                  foreground="gray", font=("Consolas", 9))
        self.lbl_info.pack(anchor="w")

        # ----- trim -----
        trim_f = ttk.LabelFrame(frm,
                                text="Trim  (HH:MM:SS.mmm — leave blank to use full file)",
                                padding=8)
        trim_f.pack(fill="x", pady=(0, 6))
        ttk.Label(trim_f, text="Start:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_start = tk.StringVar()
        ttk.Entry(trim_f, textvariable=self.var_start, width=16).grid(
            row=0, column=1, sticky="w", padx=6)
        ttk.Label(trim_f, text="End:").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.var_end = tk.StringVar()
        ttk.Entry(trim_f, textvariable=self.var_end, width=16).grid(
            row=0, column=3, sticky="w", padx=6)
        ttk.Label(trim_f,
                  text="Both blank = full file.  End = end of file when start only is given.",
                  foreground="gray").grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 0))

        # ----- gain -----
        gain_f = ttk.LabelFrame(frm, text="Gain adjustment", padding=8)
        gain_f.pack(fill="x", pady=(0, 6))
        ttk.Label(gain_f, text="Volume (dB):").grid(row=0, column=0, sticky="w")
        self.var_gain = tk.DoubleVar(value=0.0)
        ttk.Spinbox(gain_f, from_=-24.0, to=24.0, increment=0.5,
                    textvariable=self.var_gain, width=8, format="%.1f").grid(
                        row=0, column=1, sticky="w", padx=6)
        ttk.Label(gain_f, text="0.0 dB = no change",
                  foreground="gray").grid(row=0, column=2, sticky="w", padx=12)

        # ----- output -----
        out_f = ttk.LabelFrame(
            frm,
            text="Output  (DSD is decoded to high-resolution PCM — no DSD re-encoding)",
            padding=8)
        out_f.pack(fill="x", pady=(0, 6))

        ttk.Label(out_f, text="Format:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_out_fmt = tk.StringVar(value="FLAC")
        fmt_cb = ttk.Combobox(out_f, textvariable=self.var_out_fmt, state="readonly",
                               width=8, values=["FLAC", "WAV"])
        fmt_cb.grid(row=0, column=1, sticky="w", padx=6)
        self.var_out_fmt.trace_add("write", lambda *_: self._update_out_ext())

        ttk.Label(out_f, text="Sample rate (Hz):").grid(
            row=0, column=2, sticky="w", padx=(12, 0))
        self.var_out_sr = tk.StringVar(value="96000")
        ttk.Combobox(out_f, textvariable=self.var_out_sr, state="readonly",
                     width=10, values=_PCM_RATES).grid(row=0, column=3, sticky="w", padx=6)

        ttk.Label(out_f, text="Bit depth:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_out_bits = tk.StringVar(value="24")
        ttk.Combobox(out_f, textvariable=self.var_out_bits, state="readonly",
                     width=6, values=_BIT_DEPTHS).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(out_f, text="Output file:").grid(row=2, column=0, sticky="w", pady=4)
        self.var_outfile = tk.StringVar()
        ttk.Entry(out_f, textvariable=self.var_outfile, width=42).grid(
            row=2, column=1, columnspan=3, sticky="ew", padx=6)
        ttk.Button(out_f, text="…", width=3, command=self._pick_outfile).grid(
            row=2, column=4, sticky="w")
        out_f.columnconfigure(3, weight=1)

        # ----- actions -----
        act = ttk.Frame(frm)
        act.pack(fill="x", pady=(8, 0))
        self.lbl_status = ttk.Label(act, text="", anchor="w", foreground="gray")
        self.lbl_status.pack(side="left", fill="x", expand=True)
        ttk.Button(act, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(act, text="Start", command=self._start).pack(side="right", padx=4)

        # Pre-load from drag-and-drop
        if initial_files:
            dsd = [f for f in initial_files
                   if Path(f).is_file() and Path(f).suffix.lower() in DSD_EXTS]
            if dsd:
                self.var_src.set(dsd[0])
                self._suggest_outfile()
                self.after(50, self._load_info)

    # ----- pickers -----

    def _pick_source(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Select DSD file",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("DSD files", ("*.dff", "*.dsf")), ("All files", "*.*")],
        )
        if not f:
            return
        self.var_src.set(f)
        self._suggest_outfile()
        self.config_obj["last_input_dir"] = str(Path(f).parent)
        self.config_obj.save()
        self._load_info()

    def _suggest_outfile(self) -> None:
        src = self.var_src.get().strip()
        if not src:
            return
        p = Path(src)
        ext = ".flac" if self.var_out_fmt.get() == "FLAC" else ".wav"
        self.var_outfile.set(str(p.with_suffix(ext)))

    def _update_out_ext(self) -> None:
        cur = self.var_outfile.get().strip()
        ext = ".flac" if self.var_out_fmt.get() == "FLAC" else ".wav"
        if cur:
            self.var_outfile.set(str(Path(cur).with_suffix(ext)))
        else:
            self._suggest_outfile()

    def _pick_outfile(self) -> None:
        ext = ".flac" if self.var_out_fmt.get() == "FLAC" else ".wav"
        f = filedialog.asksaveasfilename(
            parent=self, title="Save output file",
            defaultextension=ext,
            initialdir=self.config_obj.get("last_output_dir") or None,
            filetypes=[("FLAC", "*.flac"), ("WAV", "*.wav"), ("All files", "*.*")],
        )
        if f:
            self.var_outfile.set(f)

    # ----- probe -----

    def _load_info(self) -> None:
        src = self.var_src.get().strip()
        if not src:
            return
        ffprobe = get_tool("ffprobe").path(self.config_obj)
        if not ffprobe.exists():
            self.lbl_info.configure(
                text="ffprobe.exe not found — install via Tools → Update all CLI tools",
                foreground="#b03030")
            return
        self.lbl_info.configure(text="Loading…", foreground="gray")
        threading.Thread(target=self._probe_worker,
                         args=(src, ffprobe), daemon=True).start()

    def _probe_worker(self, src: str, ffprobe: Path) -> None:
        try:
            r = subprocess.run(
                [str(ffprobe), "-v", "error", "-show_format", "-show_streams",
                 "-of", "json", src],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            j = json.loads(r.stdout or "{}")
        except Exception as e:
            self.after(0, lambda: self.lbl_info.configure(
                text=f"Probe error: {e}", foreground="#b03030"))
            return

        fmt = j.get("format") or {}
        streams = j.get("streams") or []
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

        duration = float(fmt.get("duration") or audio.get("duration") or 0)
        sr = int(audio.get("sample_rate") or 0)
        channels = int(audio.get("channels") or 0)
        codec = (audio.get("codec_name") or "?").upper()
        size_bytes = int(fmt.get("size") or 0)
        bitrate_kbps = int(float(fmt.get("bit_rate") or 0)) // 1000

        self._duration = duration
        self._src_sample_rate = sr

        rate_label = _DSD_RATE_LABELS.get(sr, f"{sr:,} Hz")
        dur_str = _sec_to_hms(duration)
        size_str = f"{size_bytes / (1024 ** 2):.1f} MiB" if size_bytes else "?"
        br_str = f"{bitrate_kbps:,} kbps" if bitrate_kbps else "?"

        info_text = (
            f"Codec: {codec}  |  {rate_label}  |  {channels} ch\n"
            f"Duration: {dur_str}  |  Size: {size_str}  |  Bitrate: {br_str}"
        )
        self.after(0, lambda: self._apply_probe_result(info_text, duration))

    def _apply_probe_result(self, info_text: str, duration: float) -> None:
        self.lbl_info.configure(text=info_text, foreground="gray")
        if duration and not self.var_end.get():
            self.var_end.set(_sec_to_hms(duration))

    # ----- start -----

    def _start(self) -> None:
        src_str = self.var_src.get().strip()
        if not src_str:
            messagebox.showwarning("Trader's Little Jedi", "Select a DSD source file first.")
            return
        src = Path(src_str)
        if not src.is_file():
            messagebox.showerror("Trader's Little Jedi", f"File not found:\n{src}")
            return

        out_str = self.var_outfile.get().strip()
        if not out_str:
            messagebox.showwarning("Trader's Little Jedi", "Choose an output file first.")
            return
        out = Path(out_str)

        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                "ffmpeg.exe not found. Open Tools → Update all CLI tools to install.",
            )
            return

        start_str = self.var_start.get().strip()
        end_str = self.var_end.get().strip()
        start_sec = parse_time_str(start_str) if start_str else None
        end_sec = parse_time_str(end_str) if end_str else None

        if start_str and start_sec is None:
            messagebox.showerror("Trader's Little Jedi",
                                 f"Cannot parse start time: '{start_str}'")
            return
        if end_str and end_sec is None:
            messagebox.showerror("Trader's Little Jedi",
                                 f"Cannot parse end time: '{end_str}'")
            return
        if start_sec is not None and end_sec is not None and end_sec <= start_sec:
            messagebox.showerror("Trader's Little Jedi", "End time must be after start time.")
            return

        gain_db = float(self.var_gain.get())
        fmt = self.var_out_fmt.get()
        sr = self.var_out_sr.get()
        bits = int(self.var_out_bits.get())

        out.parent.mkdir(parents=True, exist_ok=True)

        args: list[str] = [str(ffmpeg), "-y", "-hide_banner"]
        if start_sec is not None:
            args += ["-ss", f"{start_sec:.6f}"]
        args += ["-i", str(src)]

        if end_sec is not None:
            if start_sec is not None:
                args += ["-t", f"{end_sec - start_sec:.6f}"]
            else:
                args += ["-t", f"{end_sec:.6f}"]

        filters: list[str] = []
        if abs(gain_db) > 0.001:
            filters.append(f"volume={gain_db:.2f}dB")
        if filters:
            args += ["-af", ",".join(filters)]

        args += ["-ar", sr]

        if fmt == "FLAC":
            level = self.config_obj.get("flac_compression_level", 8)
            if bits <= 16:
                args += ["-c:a", "flac", "-sample_fmt", "s16",
                         "-compression_level", str(level)]
            else:
                args += ["-c:a", "flac", "-sample_fmt", "s32",
                         "-bits_per_raw_sample", str(bits),
                         "-compression_level", str(level)]
        else:
            pcm = {"16": "pcm_s16le", "24": "pcm_s24le", "32": "pcm_s32le"}.get(
                str(bits), "pcm_s24le")
            args += ["-c:a", pcm]

        args.append(str(out))

        if not self.runner.run_sequence([args]):
            messagebox.showwarning("Trader's Little Jedi", "Another job is already running.")
            return
        self.destroy()


def _sec_to_hms(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"
