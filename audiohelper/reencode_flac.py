"""Re-encode FLAC files at a different compression level, preserving all
metadata (Vorbis comments, PICTURE blocks, replaygain, etc.) via ffmpeg.
Optional post-write integrity check uses `flac -t`."""

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool


class ReencodeFlacDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Re-encode FLAC files")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("680x480")

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Files to re-encode:").grid(row=0, column=0, sticky="w")
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
        ttk.Button(btn_row, text="Clear",
                   command=lambda: self.listbox.delete(0, "end")).pack(side="left", padx=4)

        opt = ttk.LabelFrame(frm, text="Options", padding=8)
        opt.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(opt, text="Compression level (0 fast … 8 best):").grid(row=0, column=0, sticky="w", pady=2)
        self.var_level = tk.IntVar(value=int(self.config_obj.get("flac_compression_level", 8)))
        ttk.Combobox(opt, textvariable=self.var_level, width=5, state="readonly",
                     values=list(range(0, 9))).grid(row=0, column=1, sticky="w", padx=6)
        self.var_verify = tk.BooleanVar(value=bool(self.config_obj.get("flac_verify", True)))
        ttk.Checkbutton(opt, text="Verify output integrity with flac -t after encoding",
                        variable=self.var_verify).grid(row=1, column=0, columnspan=3, sticky="w", pady=2)
        self.var_inplace = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Replace input file in place "
                        "(uncheck to write *.reencoded.flac alongside)",
                        variable=self.var_inplace).grid(row=2, column=0, columnspan=3, sticky="w")
        ttk.Label(opt, text="All metadata (tags, cover art, ReplayGain) is preserved.",
                  foreground="gray").grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        act = ttk.Frame(frm)
        act.grid(row=4, column=0, columnspan=4, sticky="e", pady=(10, 0))
        ttk.Button(act, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(act, text="Start", command=self._start).pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        if initial_files:
            for f in initial_files:
                if Path(f).suffix.lower() == ".flac":
                    self.listbox.insert("end", f)

    def _add_files(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Select FLAC files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("FLAC files", "*.flac"), ("All files", "*.*")],
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
        for p in sorted(Path(d).rglob("*.flac")):
            self.listbox.insert("end", str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()

    def _remove_selected(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    def _start(self) -> None:
        files = list(self.listbox.get(0, "end"))
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some FLAC files first.")
            return

        ffmpeg = get_tool("ffmpeg")
        ffmpeg_exe = ffmpeg.path(self.config_obj)
        if not ffmpeg_exe.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                f"ffmpeg.exe not found at:\n{ffmpeg_exe}\n\n"
                "Open Tools → Update all CLI tools to install it.",
            )
            return

        flac = get_tool("flac")
        flac_exe = flac.path(self.config_obj)
        verify = self.var_verify.get()
        if verify and not flac_exe.exists():
            if messagebox.askyesno(
                "Trader's Little Jedi",
                "flac.exe is not installed, so post-encode verification cannot run.\n\n"
                "Proceed without verification?",
                default="no",
            ) is False:
                return
            verify = False

        level = int(self.var_level.get())
        inplace = self.var_inplace.get()
        self.config_obj["flac_compression_level"] = level
        self.config_obj["flac_verify"] = self.var_verify.get()
        self.config_obj.save()

        jobs: list = []
        for f in files:
            src = Path(f)
            if inplace:
                tmp = src.with_suffix(".reencode-tmp.flac")
                jobs.append(_ffmpeg_reencode(ffmpeg_exe, src, tmp, level))
                if verify and flac_exe.exists():
                    jobs.append([str(flac_exe), "-t", "-s", str(tmp)])
                jobs.append(_replace_in_place_job(src, tmp))
            else:
                out = src.with_name(src.stem + ".reencoded.flac")
                jobs.append(_ffmpeg_reencode(ffmpeg_exe, src, out, level))
                if verify and flac_exe.exists():
                    jobs.append([str(flac_exe), "-t", "-s", str(out)])

        if not self.runner.run_sequence(jobs):
            messagebox.showwarning("Trader's Little Jedi", "Another job is already running.")
            return
        self.destroy()


def _ffmpeg_reencode(ffmpeg_exe: Path, src: Path, dst: Path, level: int) -> list[str]:
    return [
        str(ffmpeg_exe), "-y", "-hide_banner", "-i", str(src),
        "-map", "0:a", "-map", "0:v?",
        "-c:a", "flac", "-compression_level", str(level),
        "-c:v", "copy",
        "-map_metadata", "0",
        str(dst),
    ]


def _replace_in_place_job(src: Path, tmp: Path):
    def replace_in_place():
        """atomically replace original FLAC with the re-encoded temp file"""
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError(f"expected output not found or empty: {tmp}")
        os.replace(tmp, src)
    replace_in_place.__doc__ = f"replace {src.name} with re-encoded copy"
    return replace_in_place
