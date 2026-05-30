"""Test WAV files for hidden MPEG content.

Two-pass analysis
-----------------
Pass 1 (ffprobe)  — reads the audio codec stored in the WAV container.
  A codec other than pcm_* means the WAV is wrapping a non-PCM stream
  (e.g. MP3-in-WAV, a clear red flag).

Pass 2 (aucdtect) — if aucdtect.exe is installed, runs psychoacoustic
  analysis which detects down-sampling artefacts from lossy sources even
  when the WAV container contains normal PCM data.
"""

import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool

_PCM_CODECS = frozenset({
    "pcm_s16le", "pcm_s16be", "pcm_s24le", "pcm_s24be",
    "pcm_s32le", "pcm_s32be", "pcm_f32le", "pcm_f64le",
    "pcm_u8", "pcm_alaw", "pcm_mulaw",
})


class TestMpegDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Test WAV files for MPEG content")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("1020x560")

        self._cancel = threading.Event()

        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Button(top, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(top, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(top, text="Clear", command=self._clear).pack(side="left", padx=4)
        self.btn_start = ttk.Button(top, text="Test", command=self._start)
        self.btn_start.pack(side="right")
        self.btn_cancel = ttk.Button(top, text="Cancel", command=self._do_cancel,
                                     state="disabled")
        self.btn_cancel.pack(side="right", padx=4)

        aucdtect_path = get_tool("aucdtect").path(config)
        note = ("auCDtect available — psychoacoustic analysis enabled."
                if aucdtect_path.exists()
                else "auCDtect not found — codec-only check.  "
                     "Install aucdtect.exe to tools\\ for full analysis.")
        ttk.Label(self, text=note, foreground="gray",
                  padding=(8, 2, 8, 2)).pack(anchor="w")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=4)

        cols = ("file", "codec", "pcm", "aucdtect", "result", "message")
        widths = (310, 100, 50, 80, 70, 350)
        self.tv = ttk.Treeview(body, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, widths):
            self.tv.heading(c, text=c.title())
            self.tv.column(c, width=w, anchor="w")
        ys = ttk.Scrollbar(body, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)

        self.tv.tag_configure("ok",   foreground="#2c7a2c")
        self.tv.tag_configure("warn", foreground="#9a8400")
        self.tv.tag_configure("fail", foreground="#b03030")
        self.tv.tag_configure("skip", foreground="#888")

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=8)
        self.status = ttk.Label(self, text="Ready", anchor="w",
                                relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        if initial_files:
            for f in initial_files:
                p = Path(f)
                if p.is_file() and p.suffix.lower() in {".wav", ".bwf"}:
                    self._add_row(f)
            self._update_idle()

    # ----- file list -----

    def _add_row(self, path: str) -> None:
        self.tv.insert("", "end", values=(path, "", "", "", "pending", ""))

    def _add_files(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Select WAV / BWF files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("WAV / BWF", ("*.wav", "*.bwf")), ("All files", "*.*")],
        )
        for f in files:
            self._add_row(f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
        self._update_idle()

    def _add_folder(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and p.suffix.lower() in {".wav", ".bwf"}:
                self._add_row(str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        self._update_idle()

    def _remove(self) -> None:
        for iid in self.tv.selection():
            self.tv.delete(iid)
        self._update_idle()

    def _clear(self) -> None:
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        self._update_idle()

    def _update_idle(self) -> None:
        self.status.configure(text=f"{len(self.tv.get_children())} file(s) queued")

    def _do_cancel(self) -> None:
        self._cancel.set()

    # ----- run -----

    def _start(self) -> None:
        rows = list(self.tv.get_children())
        if not rows:
            messagebox.showwarning("Trader's Little Jedi", "Add some WAV files first.")
            return
        for iid in rows:
            vals = list(self.tv.item(iid, "values"))
            self.tv.item(iid, values=(vals[0], "", "", "", "pending", ""), tags=())
        self._cancel.clear()
        self.btn_start.state(["disabled"])
        self.btn_cancel.state(["!disabled"])
        self.progress.configure(maximum=len(rows), value=0)
        threading.Thread(target=self._worker, args=(rows,), daemon=True).start()

    def _worker(self, rows: list[str]) -> None:
        ffprobe = get_tool("ffprobe").path(self.config_obj)
        aucdtect = get_tool("aucdtect").path(self.config_obj)
        ok = warn = fail = skip = 0
        try:
            for i, iid in enumerate(rows, 1):
                if self._cancel.is_set():
                    self._upd(iid, result="cancelled", tag="skip")
                    skip += 1
                    continue
                path = self.tv.item(iid, "values")[0]
                self._set_status(f"[{i}/{len(rows)}] {Path(path).name}")
                self._upd(iid, result="testing…")

                codec, is_pcm = self._probe_codec(path, ffprobe) if ffprobe.exists() else ("?", None)
                aucd_txt = ""
                if aucdtect.exists() and is_pcm is True:
                    aucd_txt = self._run_aucdtect(path, aucdtect)

                if is_pcm is False:
                    self._upd(iid, codec=codec, pcm="NO", aucdtect=aucd_txt,
                              result="FAIL",
                              message=f"Non-PCM audio in WAV container: {codec}",
                              tag="fail")
                    fail += 1
                elif aucd_txt and "MPEG" in aucd_txt.upper():
                    self._upd(iid, codec=codec, pcm="yes", aucdtect=aucd_txt,
                              result="WARN",
                              message="auCDtect: likely lossy origin",
                              tag="warn")
                    warn += 1
                elif is_pcm is True:
                    msg = "PCM OK"
                    if aucd_txt:
                        msg += f" | auCDtect: {aucd_txt}"
                    self._upd(iid, codec=codec, pcm="yes", aucdtect=aucd_txt,
                              result="OK", message=msg, tag="ok")
                    ok += 1
                else:
                    self._upd(iid, codec="?", pcm="?",
                              result="skip", message="ffprobe not available", tag="skip")
                    skip += 1
                self._tick(i)
        finally:
            self.after(0, lambda: self._finish(ok, warn, fail, skip))

    def _probe_codec(self, path: str, ffprobe: Path) -> tuple[str, bool | None]:
        try:
            r = subprocess.run(
                [str(ffprobe), "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=codec_name",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            return "?", None
        codec = (r.stdout or "").strip().lower()
        if not codec:
            return "?", None
        return codec, (codec in _PCM_CODECS)

    def _run_aucdtect(self, path: str, aucdtect: Path) -> str:
        try:
            r = subprocess.run(
                [str(aucdtect), path],
                capture_output=True, text=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            return f"error: {e}"
        out = (r.stdout or "") + (r.stderr or "")
        for ln in reversed(out.splitlines()):
            s = ln.strip()
            if s and ("probability" in s.lower() or "cdda" in s.lower()
                      or "mpeg" in s.lower()):
                return s[:90]
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return lines[-1][:90] if lines else "no output"

    def _upd(self, iid: str, codec: str | None = None, pcm: str | None = None,
             aucdtect: str | None = None, result: str | None = None,
             message: str | None = None, tag: str | None = None) -> None:
        def f():
            if not self.tv.exists(iid):
                return
            v = list(self.tv.item(iid, "values"))
            if codec   is not None: v[1] = codec
            if pcm     is not None: v[2] = pcm
            if aucdtect is not None: v[3] = aucdtect
            if result  is not None: v[4] = result
            if message is not None: v[5] = message
            kw: dict = {"values": v}
            if tag is not None:
                kw["tags"] = (tag,) if tag else ()
            self.tv.item(iid, **kw)
        self.after(0, f)

    def _set_status(self, text: str) -> None:
        self.after(0, lambda: self.status.configure(text=text))

    def _tick(self, v: int) -> None:
        self.after(0, lambda: self.progress.configure(value=v))

    def _finish(self, ok: int, warn: int, fail: int, skip: int) -> None:
        self.btn_start.state(["!disabled"])
        self.btn_cancel.state(["disabled"])
        total = ok + warn + fail + skip
        prefix = "Cancelled.  " if self._cancel.is_set() else "Done.  "
        self.status.configure(
            text=f"{prefix}{ok} OK,  {warn} WARN,  {fail} FAIL,  "
                 f"{skip} skipped  ({total} total)"
        )
