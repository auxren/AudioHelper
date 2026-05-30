"""ReplayGain scanner — uses metaflac (FLAC files only).

Album gain   — one metaflac call with all files; writes both
               REPLAYGAIN_TRACK_* and REPLAYGAIN_ALBUM_* tags.
Track gain   — one metaflac call per file; writes only
               REPLAYGAIN_TRACK_* tags.
Remove       — strips all REPLAYGAIN_* tags from each file.

All operations are in-place metadata writes (no audio re-encoding).
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool

_FLAC_EXTS = {".flac"}


class ReplayGainDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Scan ReplayGain")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("680x460")
        self.minsize(560, 380)

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ── file list ────────────────────────────────────────────────
        ttk.Label(frm, text="FLAC files to scan:").grid(
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

        # ── mode ─────────────────────────────────────────────────────
        mf = ttk.LabelFrame(frm, text="Mode", padding=8)
        mf.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.var_mode = tk.StringVar(value=config.get("replaygain_mode", "album"))
        ttk.Radiobutton(
            mf,
            text="Album gain  —  all files processed together as one album "
                 "(sets REPLAYGAIN_TRACK_* and REPLAYGAIN_ALBUM_* tags)",
            variable=self.var_mode, value="album",
        ).pack(anchor="w")
        ttk.Radiobutton(
            mf,
            text="Track gain only  —  each file processed independently "
                 "(sets REPLAYGAIN_TRACK_* tags only, no album reference)",
            variable=self.var_mode, value="track",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Radiobutton(
            mf,
            text="Remove ReplayGain tags  —  strips all REPLAYGAIN_* tags from each file",
            variable=self.var_mode, value="remove",
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(mf,
                  text="Tags are written directly to the source files "
                       "(in-place metadata rewrite, no audio re-encoding).",
                  foreground="gray").pack(anchor="w", pady=(8, 0))

        # ── status + buttons ──────────────────────────────────────────
        self.lbl_status = ttk.Label(frm, text="", foreground="gray")
        self.lbl_status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        act = ttk.Frame(frm)
        act.grid(row=4, column=2, columnspan=2, sticky="e", pady=(10, 0))
        self.btn_cancel = ttk.Button(act, text="Cancel", command=self.destroy)
        self.btn_cancel.pack(side="right")
        self.btn_scan = ttk.Button(act, text="Scan", command=self._start)
        self.btn_scan.pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.columnconfigure(0, weight=1)

        if initial_files:
            for f in initial_files:
                if Path(f).suffix.lower() in _FLAC_EXTS:
                    self.listbox.insert("end", f)

    # ── helpers ──────────────────────────────────────────────────────

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
            if p.is_file():
                self.listbox.insert("end", str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()

    def _remove(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    # ── start ────────────────────────────────────────────────────────

    def _start(self) -> None:
        files = list(self.listbox.get(0, "end"))
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some FLAC files first.")
            return

        metaflac = get_tool("metaflac").path(self.config_obj)
        if not metaflac.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                f"metaflac not found at:\n{metaflac}\n\n"
                "Install via Tools → Update all CLI tools.",
            )
            return

        mode = self.var_mode.get()
        self.config_obj["replaygain_mode"] = mode
        self.config_obj.save()

        jobs: list[list[str]] = []
        if mode == "album":
            # Single call with all files — metaflac computes album gain across them
            jobs.append([str(metaflac), "--add-replay-gain"] + files)
        elif mode == "track":
            # One call per file — no cross-file album reference
            for f in files:
                jobs.append([str(metaflac), "--add-replay-gain", f])
        else:  # remove
            for f in files:
                jobs.append([str(metaflac), "--remove-replay-gain", f])

        n = len(files)
        mode_label = {"album": "Album gain", "track": "Track gain", "remove": "Remove tags"}[mode]
        self.lbl_status.configure(text=f"{mode_label} — scanning {n} file(s)…")
        self.btn_scan.configure(state="disabled")

        def _done(rc: int) -> None:
            def _ui() -> None:
                if rc == 0:
                    self.lbl_status.configure(
                        text=f"Done. {mode_label} applied to {n} file(s).",
                        foreground="green",
                    )
                else:
                    self.lbl_status.configure(
                        text=f"Completed with errors (exit {rc}). Check the log.",
                        foreground="red",
                    )
                self.btn_scan.configure(state="normal")
                self.btn_cancel.configure(text="Close")
            self.after(0, _ui)

        if not self.runner.run_sequence(jobs, on_done=_done):
            messagebox.showwarning("Trader's Little Jedi", "Another job is already running.")
            self.lbl_status.configure(text="")
            self.btn_scan.configure(state="normal")
            return
