"""Test encoded audio files for integrity.

Per-format engine selection:
  .flac   -> flac -t -s    (decode + verify CRC + STREAMINFO MD5)
  .ape    -> mac <file> -v (Monkey's Audio verify mode)
  others  -> ffmpeg -v error -i <file> -f null -  (decode through null muxer)

A non-zero exit means FAIL. For ffmpeg, decoder warnings (rc=0 + stderr) become WARN."""

import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool


class TestEncodedDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Test encoded audio files")
        self.config_obj = config
        self.runner = runner  # unused; we manage our own worker
        self.transient(parent)
        self.geometry("900x580")

        self._cancel = threading.Event()
        self._worker_thread: threading.Thread | None = None

        # ----- toolbar -----
        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Button(top, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(top, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(top, text="Clear", command=self._clear).pack(side="left", padx=4)
        self.btn_start = ttk.Button(top, text="Start", command=self._start)
        self.btn_start.pack(side="right")
        self.btn_cancel = ttk.Button(top, text="Cancel test", command=self._cancel_now,
                                     state="disabled")
        self.btn_cancel.pack(side="right", padx=4)

        # ----- results -----
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        cols = ("file", "format", "engine", "status", "message")
        widths = (340, 60, 110, 80, 280)
        self.tv = ttk.Treeview(body, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, widths):
            self.tv.heading(c, text=c.title())
            self.tv.column(c, width=w, anchor="w")
        ys = ttk.Scrollbar(body, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)

        self.tv.tag_configure("ok", foreground="#2c7a2c")
        self.tv.tag_configure("fail", foreground="#b03030")
        self.tv.tag_configure("warn", foreground="#9a8400")
        self.tv.tag_configure("skip", foreground="#888")

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=8)
        self.status = ttk.Label(self, text="Ready", anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        if initial_files:
            from .action_picker import AUDIO_EXTS
            for f in initial_files:
                if Path(f).is_file() and Path(f).suffix.lower() in AUDIO_EXTS:
                    self._add_row(f)
            self._update_idle_status()

    # ----- file list -----

    def _add_row(self, path: str) -> str:
        ext = Path(path).suffix.lower().lstrip(".") or "?"
        return self.tv.insert("", "end", values=(path, ext, "", "pending", ""))

    def _add_files(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio files", ("*.flac", "*.mp3", "*.wav", "*.bwf", "*.ape", "*.shn",
                                 "*.dff", "*.dsf", "*.aiff", "*.aif", "*.wv", "*.tta",
                                 "*.m4a", "*.aac", "*.ogg", "*.opus", "*.wma")),
                ("All files", "*.*"),
            ],
        )
        for f in files:
            self._add_row(f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
        self._update_idle_status()

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
                self._add_row(str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        self._update_idle_status()

    def _remove(self):
        for iid in self.tv.selection():
            self.tv.delete(iid)
        self._update_idle_status()

    def _clear(self):
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        self._update_idle_status()

    def _update_idle_status(self):
        n = len(self.tv.get_children())
        self.status.configure(text=f"{n} file(s) queued")

    # ----- run -----

    def _cancel_now(self):
        self._cancel.set()
        self.status.configure(text="Cancel requested — finishing current file…")

    def _start(self):
        rows = list(self.tv.get_children())
        if not rows:
            messagebox.showwarning("Trader's Little Jedi", "Add some files first.")
            return
        # Reset status of all rows
        for iid in rows:
            vals = list(self.tv.item(iid, "values"))
            vals[2] = ""
            vals[3] = "pending"
            vals[4] = ""
            self.tv.item(iid, values=vals, tags=())
        self._cancel.clear()
        self.btn_start.state(["disabled"])
        self.btn_cancel.state(["!disabled"])
        self.progress.configure(maximum=len(rows), value=0)
        self._worker_thread = threading.Thread(target=self._worker, args=(rows,), daemon=True)
        self._worker_thread.start()

    def _worker(self, rows: list[str]):
        # Resolve engines once
        flac_exe = get_tool("flac").path(self.config_obj)
        mac_exe = get_tool("mac").path(self.config_obj)
        ffmpeg_exe = get_tool("ffmpeg").path(self.config_obj)
        passed = failed = warned = skipped = 0
        try:
            for i, iid in enumerate(rows, 1):
                if self._cancel.is_set():
                    self._set_row(iid, status="cancelled", tag="skip")
                    skipped += 1
                    continue
                path = self.tv.item(iid, "values")[0]
                if not Path(path).is_file():
                    self._set_row(iid, status="missing", message="file no longer exists", tag="skip")
                    skipped += 1
                    self._tick(i)
                    continue
                self._set_row(iid, status="testing…", tag="")
                self._set_status(f"[{i}/{len(rows)}] testing {Path(path).name}")
                ext = Path(path).suffix.lower()
                engine, result, msg = self._test_file(path, ext, flac_exe, mac_exe, ffmpeg_exe)
                if result == "ok":
                    self._set_row(iid, engine=engine, status="OK", message="", tag="ok")
                    passed += 1
                elif result == "warn":
                    self._set_row(iid, engine=engine, status="WARN", message=msg, tag="warn")
                    warned += 1
                elif result == "skip":
                    self._set_row(iid, engine=engine, status="skipped", message=msg, tag="skip")
                    skipped += 1
                else:  # fail
                    self._set_row(iid, engine=engine, status="FAIL", message=msg, tag="fail")
                    failed += 1
                self._tick(i)
        finally:
            self.after(0, lambda: self._finish(passed, failed, warned, skipped))

    def _test_file(self, path: str, ext: str, flac_exe: Path, mac_exe: Path, ffmpeg_exe: Path):
        if ext == ".flac" and flac_exe.exists():
            engine, rc, msg = self._run([str(flac_exe), "-t", "-s", path])
            if rc == 0:
                return "flac -t", "ok", ""
            return "flac -t", "fail", msg or f"exit {rc}"
        if ext == ".ape" and mac_exe.exists():
            engine, rc, msg = self._run([str(mac_exe), path, "-v"])
            if rc == 0:
                return "mac -v", "ok", ""
            return "mac -v", "fail", msg or f"exit {rc}"
        if not ffmpeg_exe.exists():
            return "(none)", "skip", "no engine available — install ffmpeg"
        engine, rc, msg = self._run([
            str(ffmpeg_exe), "-v", "error", "-hide_banner", "-nostats",
            "-i", path, "-f", "null", "-",
        ])
        if rc != 0:
            return "ffmpeg null", "fail", msg or f"exit {rc}"
        if msg:
            return "ffmpeg null", "warn", msg
        return "ffmpeg null", "ok", ""

    def _run(self, argv: list[str]):
        """Run a subprocess synchronously. Returns (argv[0], rc, condensed stderr/stdout)."""
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            return (argv[0], 1, f"could not run: {e}")
        # Poll for cancel; allow up to 1 hour for huge DSD files
        deadline = 60 * 60
        elapsed = 0.0
        out = b""
        err = b""
        while True:
            try:
                o, e_ = proc.communicate(timeout=0.5)
                out += o
                err += e_
                break
            except subprocess.TimeoutExpired:
                elapsed += 0.5
                if self._cancel.is_set():
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return (argv[0], 130, "cancelled")
                if elapsed >= deadline:
                    proc.kill()
                    return (argv[0], 1, "timed out (1 hr)")
        rc = proc.returncode
        msg = _condense(err.decode("utf-8", errors="replace")) \
              or _condense(out.decode("utf-8", errors="replace"))
        return (argv[0], rc, msg)

    def _set_row(self, iid: str, engine: str | None = None, status: str | None = None,
                 message: str | None = None, tag: str | None = None):
        def f():
            if not self.tv.exists(iid):
                return
            vals = list(self.tv.item(iid, "values"))
            if engine is not None:
                vals[2] = engine
            if status is not None:
                vals[3] = status
            if message is not None:
                vals[4] = message
            if tag is not None:
                self.tv.item(iid, values=vals, tags=((tag,) if tag else ()))
            else:
                self.tv.item(iid, values=vals)
        self.after(0, f)

    def _set_status(self, text: str):
        self.after(0, lambda: self.status.configure(text=text))

    def _tick(self, value: int):
        self.after(0, lambda: self.progress.configure(value=value))

    def _finish(self, passed: int, failed: int, warned: int, skipped: int):
        self.btn_start.state(["!disabled"])
        self.btn_cancel.state(["disabled"])
        total = passed + failed + warned + skipped
        prefix = "Cancelled. " if self._cancel.is_set() else "Done. "
        self.status.configure(
            text=f"{prefix}{passed} OK, {failed} FAIL, {warned} WARN, {skipped} skipped  ({total} total)"
        )


def _condense(text: str, limit: int = 220) -> str:
    parts: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        parts.append(s)
    msg = " | ".join(parts)
    if len(msg) > limit:
        msg = msg[: limit - 1] + "…"
    return msg
