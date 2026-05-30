"""Show audio file details — wraps MediaInfo (default) or ffprobe."""

import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog
from pathlib import Path

from .tools import get_tool


class ShowDetailsDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Audio file details")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("960x600")
        # Intentionally NOT grab_set — let the user keep using the main window.

        self._cache: dict[str, str] = {}

        # ----- toolbar -----
        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Button(top, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(top, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(top, text="Clear", command=self._clear).pack(side="left", padx=4)
        ttk.Label(top, text="Backend:").pack(side="left", padx=(16, 4))
        self.var_backend = tk.StringVar(value="MediaInfo")
        backend_cb = ttk.Combobox(top, textvariable=self.var_backend, state="readonly",
                                  width=10, values=["MediaInfo", "ffprobe"])
        backend_cb.pack(side="left")
        backend_cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh(force=False))
        ttk.Button(top, text="Refresh", command=lambda: self._refresh(force=True)).pack(side="left", padx=8)
        ttk.Button(top, text="Save…", command=self._save).pack(side="right")
        ttk.Button(top, text="Copy", command=self._copy).pack(side="right", padx=4)

        # ----- split pane -----
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(paned)
        list_inner = ttk.Frame(left)
        list_inner.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(list_inner, exportselection=False, activestyle="dotbox")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_inner, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        paned.add(left, weight=1)

        right = ttk.Frame(paned)
        self.text = tk.Text(
            right, wrap="none", font=("Consolas", 9),
            background="#101418", foreground="#d8d8d8",
            insertbackground="#d8d8d8",
        )
        ys = ttk.Scrollbar(right, orient="vertical", command=self.text.yview)
        xs = ttk.Scrollbar(right, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.text.pack(fill="both", expand=True)
        paned.add(right, weight=3)

        self.status = ttk.Label(self, text="", anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        if initial_files:
            for f in initial_files:
                if Path(f).is_file():
                    self.listbox.insert("end", f)
            if self.listbox.size() > 0:
                self.listbox.selection_set(0)
                self._on_select()
        self._update_status()

    # ----- file list -----

    def _add_files(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Select files",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        for f in files:
            self.listbox.insert("end", f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
            if not self.listbox.curselection():
                self.listbox.selection_set(self.listbox.size() - len(files))
                self._on_select()
        self._update_status()

    def _add_folder(self):
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        from .action_picker import AUDIO_EXTS
        added_first = self.listbox.size()
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                self.listbox.insert("end", str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        if not self.listbox.curselection() and self.listbox.size() > added_first:
            self.listbox.selection_set(added_first)
            self._on_select()
        self._update_status()

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
        self.text.delete("1.0", "end")
        self._update_status()

    def _clear(self):
        self.listbox.delete(0, "end")
        self._cache.clear()
        self.text.delete("1.0", "end")
        self._update_status()

    # ----- backend dispatch -----

    def _cache_key(self, path: str) -> str:
        return f"{self.var_backend.get()}::{path}"

    def _on_select(self, _evt=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        path = self.listbox.get(sel[0])
        key = self._cache_key(path)
        if key in self._cache:
            self._render(self._cache[key])
            return
        self._render(f"Loading… ({Path(path).name})")
        threading.Thread(target=self._run_backend, args=(path, key), daemon=True).start()

    def _refresh(self, force: bool):
        sel = self.listbox.curselection()
        if not sel:
            return
        path = self.listbox.get(sel[0])
        if force:
            self._cache.pop(self._cache_key(path), None)
        self._on_select()

    def _run_backend(self, path: str, key: str):
        backend = key.split("::", 1)[0]
        try:
            if backend == "MediaInfo":
                tool = get_tool("mediainfo")
                exe = tool.path(self.config_obj)
                if not exe.exists():
                    text = "MediaInfo.exe not found. Install via Tools → Update all CLI tools."
                else:
                    r = subprocess.run(
                        [str(exe), "--Output=Text", path],
                        capture_output=True, text=True, timeout=30,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    text = (r.stdout or "")
                    if r.stderr:
                        text += "\n--- stderr ---\n" + r.stderr
            else:  # ffprobe
                tool = get_tool("ffprobe")
                exe = tool.path(self.config_obj)
                if not exe.exists():
                    text = "ffprobe.exe not found. Install via Tools → Update all CLI tools."
                else:
                    r = subprocess.run(
                        [str(exe), "-hide_banner", "-show_format", "-show_streams",
                         "-show_chapters", "-pretty", path],
                        capture_output=True, text=True, timeout=30,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    text = (r.stdout or "")
                    if r.stderr:
                        text += "\n--- ffprobe diagnostics ---\n" + r.stderr
            if not text.strip():
                text = "(no output)"
        except Exception as e:
            text = f"ERROR: {e}"
        self._cache[key] = text

        def apply():
            sel = self.listbox.curselection()
            if sel and self._cache_key(self.listbox.get(sel[0])) == key:
                self._render(text)
        self.after(0, apply)

    def _render(self, text: str):
        self.text.delete("1.0", "end")
        self.text.insert("end", text)
        self.text.see("1.0")
        self._update_status()

    def _update_status(self):
        sel = self.listbox.curselection()
        n = self.listbox.size()
        if sel:
            name = Path(self.listbox.get(sel[0])).name
            self.status.configure(text=f"{n} file(s) loaded  —  showing {name}")
        else:
            self.status.configure(text=f"{n} file(s) loaded")

    # ----- export -----

    def _copy(self):
        try:
            text = self.text.get("1.0", "end-1c")
        except tk.TclError:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()  # force the clipboard to actually populate
        self.status.configure(text="Copied details to clipboard")

    def _save(self):
        sel = self.listbox.curselection()
        suggested = ""
        if sel:
            p = Path(self.listbox.get(sel[0]))
            suggested = p.name + ".info.txt"
        f = filedialog.asksaveasfilename(
            parent=self, title="Save details to file",
            initialfile=suggested, defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if not f:
            return
        Path(f).write_text(self.text.get("1.0", "end-1c"), encoding="utf-8")
        self.status.configure(text=f"Saved to {f}")
