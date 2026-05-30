"""Strip audio file header / metadata.

For WAV/BWF : rewrites the file with ffmpeg -map_metadata -1, removing
             all INFO/ID3/BWF metadata chunks while keeping the PCM data.
For FLAC    : uses metaflac --remove-all-tags --dont-use-padding to clear
             all Vorbis comment and picture blocks.
For others  : ffmpeg -map_metadata -1 -c copy rewrites with a clean header.

The original file is preserved as <name>.bak unless the user opts to overwrite.
"""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .tools import get_tool


class StripHeaderDialog(tk.Toplevel):
    _open_instance: "StripHeaderDialog | None" = None

    @classmethod
    def open_or_add(cls, parent, config, runner, initial_files=None):
        """Return the existing open instance (adding files to it) or create a new one."""
        inst = cls._open_instance
        if inst is not None:
            try:
                if inst.winfo_exists():
                    if initial_files:
                        inst.add_files(initial_files)
                    inst.lift()
                    inst.focus_force()
                    return inst
            except Exception:
                pass
        return cls(parent, config, runner, initial_files)

    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        StripHeaderDialog._open_instance = self
        self.bind("<Destroy>", lambda _e: self._on_destroy())
        self.title("Strip audio file header / metadata")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("900x520")

        self._cancel = threading.Event()

        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Button(top, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(top, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(top, text="Clear", command=self._clear).pack(side="left", padx=4)
        self.btn_start = ttk.Button(top, text="Strip", command=self._start)
        self.btn_start.pack(side="right")

        # Options
        opt = ttk.LabelFrame(self, text="Options", padding=8)
        opt.pack(fill="x", padx=8, pady=4)
        self.var_backup = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt,
                        text="Keep backup (.bak) of original",
                        variable=self.var_backup).pack(anchor="w")
        ttk.Label(opt,
                  text="Removes: ID3 tags, INFO chunks, BWF metadata, Vorbis comments, cover art.",
                  foreground="gray").pack(anchor="w")

        # Results
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        cols = ("file", "format", "status", "message")
        widths = (380, 80, 80, 320)
        self.tv = ttk.Treeview(body, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, widths):
            self.tv.heading(c, text=c.title())
            self.tv.column(c, width=w, anchor="w")
        ys = ttk.Scrollbar(body, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=ys.set)
        ys.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.tag_configure("ok",   foreground="#2c7a2c")
        self.tv.tag_configure("fail", foreground="#b03030")
        self.tv.tag_configure("skip", foreground="#888")

        self.status = ttk.Label(self, text="Ready", anchor="w",
                                relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        if initial_files:
            from .action_picker import AUDIO_EXTS
            for f in initial_files:
                p = Path(f)
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                    self._add_row(f)
            self._update_idle()

    # ----- list management -----

    def _add_row(self, path: str) -> None:
        ext = Path(path).suffix.lower().lstrip(".") or "?"
        self.tv.insert("", "end", values=(path, ext, "pending", ""))

    def _add_files(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio files", ("*.wav", "*.bwf", "*.flac", "*.mp3", "*.aac",
                                 "*.m4a", "*.ogg", "*.opus", "*.aiff", "*.aif")),
                ("All files", "*.*"),
            ],
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
        from .action_picker import AUDIO_EXTS
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
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

    def add_files(self, files: list[str]) -> None:
        from .action_picker import AUDIO_EXTS
        for f in files:
            p = Path(f)
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                self._add_row(f)
        self._update_idle()

    def _on_destroy(self) -> None:
        if StripHeaderDialog._open_instance is self:
            StripHeaderDialog._open_instance = None

    def _update_idle(self) -> None:
        self.status.configure(text=f"{len(self.tv.get_children())} file(s) queued")

    # ----- run -----

    def _start(self) -> None:
        rows = list(self.tv.get_children())
        if not rows:
            messagebox.showwarning("Trader's Little Jedi", "Add some audio files first.")
            return

        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        metaflac = get_tool("metaflac").path(self.config_obj)

        if not ffmpeg.exists() and not metaflac.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                "Neither ffmpeg.exe nor metaflac.exe found.\n"
                "Open Tools → Update all CLI tools to install.",
            )
            return

        backup = self.var_backup.get()
        self.btn_start.state(["disabled"])
        threading.Thread(
            target=self._worker,
            args=(rows, ffmpeg, metaflac, backup),
            daemon=True,
        ).start()

    def _worker(self, rows: list[str], ffmpeg: Path, metaflac: Path,
                backup: bool) -> None:
        ok = fail = skip = 0
        for i, iid in enumerate(rows, 1):
            path_str = self.tv.item(iid, "values")[0]
            path = Path(path_str)
            self._set_status(f"[{i}/{len(rows)}] {path.name}")
            self._upd(iid, status="working…")

            ext = path.suffix.lower()
            try:
                if ext == ".flac" and metaflac.exists():
                    self._strip_flac(path, metaflac, backup)
                    self._upd(iid, status="OK",
                              message="Vorbis comments + pictures removed",
                              tag="ok")
                    ok += 1
                elif ffmpeg.exists():
                    self._strip_ffmpeg(path, ffmpeg, backup)
                    self._upd(iid, status="OK",
                              message="metadata removed (ffmpeg -map_metadata -1)",
                              tag="ok")
                    ok += 1
                else:
                    self._upd(iid, status="skip",
                              message=f"no engine for .{ext.lstrip('.')}",
                              tag="skip")
                    skip += 1
            except Exception as e:
                self._upd(iid, status="FAIL", message=str(e), tag="fail")
                fail += 1

        self.after(0, lambda: self._finish(ok, fail, skip))

    def _strip_ffmpeg(self, path: Path, ffmpeg: Path, backup: bool) -> None:
        tmp = path.with_name(path.stem + ".striphdr_tmp" + path.suffix)
        args = [
            str(ffmpeg), "-y", "-hide_banner", "-i", str(path),
            "-c", "copy", "-map_metadata", "-1",
            str(tmp),
        ]
        rc, err = _run_quiet(args)
        if rc != 0:
            tmp.unlink(missing_ok=True)
            detail = err.strip().splitlines()[-1] if err.strip() else ""
            raise RuntimeError(f"ffmpeg exited {rc}" + (f": {detail}" if detail else ""))
        if not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("ffmpeg produced no output")
        if backup:
            bak = path.with_suffix(path.suffix + ".bak")
            if bak.exists():
                bak.unlink()
            path.rename(bak)
        os.replace(tmp, path)

    def _strip_flac(self, path: Path, metaflac: Path, backup: bool) -> None:
        if backup:
            bak = path.with_suffix(".flac.bak")
            import shutil
            shutil.copy2(path, bak)
        # Use a single major operation; mixing shorthand (--remove-all-tags) with
        # major ops (--remove --block-type) causes "may not mix" error in metaflac.
        rc, err = _run_quiet([
            str(metaflac),
            "--remove",
            "--block-type=VORBIS_COMMENT,PICTURE,PADDING",
            "--dont-use-padding",
            str(path),
        ])
        if rc != 0:
            detail = err.strip().splitlines()[0] if err.strip() else ""
            raise RuntimeError(f"metaflac exited {rc}" + (f": {detail}" if detail else ""))

    def _upd(self, iid: str, status: str | None = None,
             message: str | None = None, tag: str | None = None) -> None:
        def f():
            if not self.tv.exists(iid):
                return
            v = list(self.tv.item(iid, "values"))
            if status  is not None: v[2] = status
            if message is not None: v[3] = message
            kw: dict = {"values": v}
            if tag is not None:
                kw["tags"] = (tag,) if tag else ()
            self.tv.item(iid, **kw)
        self.after(0, f)

    def _set_status(self, text: str) -> None:
        self.after(0, lambda: self.status.configure(text=text))

    def _finish(self, ok: int, fail: int, skip: int) -> None:
        self.btn_start.state(["!disabled"])
        total = ok + fail + skip
        self.status.configure(
            text=f"Done.  {ok} OK,  {fail} failed,  {skip} skipped  ({total} total)"
        )


def _run_quiet(args: list[str]) -> tuple[int, str]:
    import subprocess
    r = subprocess.run(
        args,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stderr = (r.stderr or b"").decode("utf-8", errors="replace")
    return r.returncode, stderr
