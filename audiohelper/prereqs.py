"""Startup prerequisite checker and auto-installer.

Required tools are downloaded and installed into the tools\\ directory the first
time the app runs (or any time they are found to be missing).

Required tools
--------------
ffmpeg / ffprobe  — conversion, DSD, merge, tag reading   (auto-install)
flac / metaflac   — FLAC encode/decode/test/ReplayGain    (auto-install)
lame              — MP3 encoding                           (auto-install)
mediainfo         — detailed file properties               (auto-install)

Optional tools (manual install from their homepages)
----------------------------------------------------
spek              — spectral analysis GUI
tascam_hireseditor — TASCAM Hi-Res Editor GUI
korg_audiogate    — KORG AudioGate GUI
xrecode3          — DSD batch converter (paid)
sox               — legacy SoX utilities                   (auto-install)
shorten           — SHN decode (legacy / abandoned)
aucdtect          — lossy-in-lossless detection (legacy / abandoned)
"""

import threading
import tkinter as tk
from tkinter import ttk

from .tools import get_tool

# Tools that must be present for full functionality; shown in the prereq dialog.
# mediainfo is intentionally NOT here — it's optional (only the "Show file
# details" analysis view uses it; ffprobe covers everything else). Listing it
# made the startup prereq dialog nag on every launch when it couldn't be
# auto-installed on macOS. It can still be installed from Tools → Update all
# CLI tools, or on demand when the details view is opened.
REQUIRED_TOOL_NAMES = [
    "ffmpeg",
    "ffprobe",
    "flac",
    "metaflac",
    "lame",
]


def check_missing(config) -> list[str]:
    """Return names of required tools that are not present on disk."""
    return [
        name
        for name in REQUIRED_TOOL_NAMES
        if not get_tool(name).path(config).exists()
    ]


class PrereqDialog(tk.Toplevel):
    """Shown on startup when one or more required tools are absent."""

    def __init__(self, parent, config, log_callback):
        super().__init__(parent)
        self.title("Trader's Little Jedi — Install prerequisites")
        self.config_obj = config
        self.log_cb = log_callback
        self.transient(parent)
        self.grab_set()
        self.geometry("720x500")
        self.resizable(True, True)
        self.minsize(580, 380)

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        # ── Header ────────────────────────────────────────────────────────
        ttk.Label(
            frm,
            text="Required CLI tools are missing",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frm,
            text="Trader's Little Jedi can download and install them automatically into the tools\\ folder.",
            foreground="gray",
        ).pack(anchor="w", pady=(2, 8))

        # ── Tool status table ─────────────────────────────────────────────
        tv_frame = ttk.Frame(frm)
        tv_frame.pack(fill="both", expand=True)
        cols = ("tool", "description", "status")
        self.tv = ttk.Treeview(
            tv_frame, columns=cols, show="headings", height=10, selectmode="none"
        )
        self.tv.heading("tool",        text="Tool",        anchor="w")
        self.tv.heading("description", text="Description", anchor="w")
        self.tv.heading("status",      text="Status",      anchor="w")
        self.tv.column("tool",        width=110, anchor="w", stretch=False)
        self.tv.column("description", width=360, anchor="w", stretch=True)
        self.tv.column("status",      width=190, anchor="w", stretch=False)
        self.tv.tag_configure("ok",      foreground="#007700")
        self.tv.tag_configure("missing", foreground="#cc0000")
        self.tv.tag_configure("manual",  foreground="#cc6600")
        self.tv.tag_configure("working", foreground="#005599")
        sb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for name in REQUIRED_TOOL_NAMES:
            t = get_tool(name)
            installed = t.path(config).exists()
            if installed:
                tag, status = "ok", "Installed"
            elif t.upstream_install:
                tag, status = "missing", "Missing — will auto-install"
            else:
                tag, status = "manual", "Missing — manual install required"
            self.tv.insert(
                "", "end", iid=name,
                values=(t.name, t.description, status),
                tags=(tag,),
            )

        # ── Status label ──────────────────────────────────────────────────
        self.lbl_status = ttk.Label(frm, text="", foreground="gray")
        self.lbl_status.pack(anchor="w", pady=(6, 0))

        # ── "Don't check" checkbox ────────────────────────────────────────
        self.var_no_check = tk.BooleanVar(
            value=bool(config.get("skip_prereq_check", False))
        )
        ttk.Checkbutton(
            frm,
            text="Don't check for missing prerequisites on startup",
            variable=self.var_no_check,
        ).pack(anchor="w", pady=(4, 0))

        # ── Buttons ───────────────────────────────────────────────────────
        act = ttk.Frame(frm)
        act.pack(fill="x", pady=(10, 0))
        ttk.Button(act, text="Skip for now", command=self._close).pack(side="right")
        self.btn_install = ttk.Button(
            act, text="Install Missing Tools", command=self._install
        )
        self.btn_install.pack(side="right", padx=4)

    # ── helpers ───────────────────────────────────────────────────────────

    def _set_row(self, name: str, status: str, tag: str) -> None:
        def _f():
            if not self.tv.exists(name):
                return
            vals = list(self.tv.item(name, "values"))
            vals[2] = status
            self.tv.item(name, values=vals, tags=(tag,))
        self.after(0, _f)

    def _close(self) -> None:
        if self.var_no_check.get():
            self.config_obj["skip_prereq_check"] = True
            self.config_obj.save()
        self.destroy()

    def _install(self) -> None:
        self.btn_install.configure(state="disabled")
        self.lbl_status.configure(text="Installing…", foreground="gray")

        def worker():
            all_ok = True
            for name in REQUIRED_TOOL_NAMES:
                t = get_tool(name)
                if t.path(self.config_obj).exists():
                    self._set_row(name, "Installed", "ok")
                    continue
                if not t.upstream_install or not t.upstream_check:
                    self._set_row(name, "Manual install required", "manual")
                    all_ok = False
                    continue
                self._set_row(name, "Checking version…", "working")
                try:
                    latest = t.upstream_check()
                    if not latest:
                        raise RuntimeError("could not determine latest version")
                    self._set_row(name, f"Downloading {latest}…", "working")
                    t.upstream_install(latest)
                    self._set_row(name, f"Installed ({latest})", "ok")
                    self.log_cb(f"[prereq] {name} installed ({latest})\n")
                except Exception as e:
                    self._set_row(name, f"Failed: {e}", "missing")
                    self.log_cb(f"[prereq] {name} install failed: {e}\n")
                    all_ok = False

            def _finish():
                if all_ok:
                    self.lbl_status.configure(
                        text="All required tools installed. Close this window to continue.",
                        foreground="green",
                    )
                    if self.var_no_check.get():
                        self.config_obj["skip_prereq_check"] = True
                        self.config_obj.save()
                else:
                    self.lbl_status.configure(
                        text="Some tools require manual installation — see homepage links.",
                        foreground="#cc6600",
                    )
                self.btn_install.configure(state="normal")

            self.after(0, _finish)

        threading.Thread(target=worker, daemon=True).start()
