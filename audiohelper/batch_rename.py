"""Batch rename audio files from embedded tag values.

Pattern tokens (case-insensitive, always uppercase in the pattern):
  {TRACKNUMBER}      raw track number from tag
  {TRACKNUMBER:2}    zero-padded to 2 digits  (or any width)
  {TITLE}  {ARTIST}  {ALBUMARTIST}  {ALBUM}  {DATE}
  {DISCNUMBER}  {GENRE}  {COMPOSER}  {PERFORMER}

The file extension is always preserved automatically.
Tag reading uses ffprobe; no audio re-encoding is performed.
"""

import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from . import tag_io
from .tools import get_tool
from .action_picker import AUDIO_EXTS

_TOKEN_RE = re.compile(r'\{([A-Za-z_]+)(?::(\d+))?\}')
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# Fields that must NOT contain bare integers — if they do, the tag is almost certainly wrong.
_MUST_NOT_BE_BARE_INT = {"TITLE", "ARTIST", "ALBUMARTIST", "ALBUM", "GENRE", "COMPOSER", "PERFORMER"}

_TOKENS_HELP = (
    "{TRACKNUMBER:2}  {TITLE}  {ARTIST}  {ALBUMARTIST}  {ALBUM}"
    "  {DATE}  {DISCNUMBER}  {GENRE}  {COMPOSER}  {PERFORMER}"
)
_DEFAULT_PATTERN = "{TRACKNUMBER:2} - {TITLE}"


def _apply_pattern(pattern: str, tags: dict) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1).upper()
        width_str = m.group(2)
        val = tags.get(key, "").strip()
        if width_str and val.isdigit():
            val = val.zfill(int(width_str))
        val = _UNSAFE_RE.sub("_", val).strip(". ")
        return val or f"[no {key.lower()}]"
    return _TOKEN_RE.sub(sub, pattern)


class BatchRenameDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Batch rename audio files")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.grab_set()
        self.geometry("920x620")
        self.minsize(740, 480)

        self._tags_cache: dict[str, dict] = {}
        self._preview_rows: list[tuple[str, str, str, str]] = []  # path, orig, new, status

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        # ── file list ────────────────────────────────────────────────
        ttk.Label(frm, text="Files to rename:").grid(row=0, column=0, sticky="w")
        lf = ttk.Frame(frm)
        lf.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=4)
        self.listbox = tk.Listbox(lf, selectmode="extended",
                                  activestyle="dotbox", height=6)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        br = ttk.Frame(frm)
        br.grid(row=2, column=0, columnspan=4, sticky="w")
        ttk.Button(br, text="Add files…",  command=self._add_files).pack(side="left")
        ttk.Button(br, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(br, text="Remove",      command=self._remove).pack(side="left")
        ttk.Button(br, text="Clear",       command=self._clear).pack(side="left", padx=4)

        # ── pattern ──────────────────────────────────────────────────
        pf = ttk.LabelFrame(frm, text="Rename pattern", padding=8)
        pf.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        prow = ttk.Frame(pf)
        prow.pack(fill="x")
        ttk.Label(prow, text="Pattern:").pack(side="left")
        self.var_pattern = tk.StringVar(
            value=config.get("batch_rename_pattern", _DEFAULT_PATTERN))
        ttk.Entry(prow, textvariable=self.var_pattern,
                  width=50).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(prow, text="Preview",
                   command=self._build_preview).pack(side="left")
        ttk.Label(pf, text=f"Tokens:  {_TOKENS_HELP}",
                  foreground="gray",
                  font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))
        ttk.Label(pf,
                  text="Extension preserved automatically.  "
                       "{TRACKNUMBER:2} zero-pads to 2 digits, :3 for 3, etc.",
                  foreground="gray",
                  font=("Segoe UI", 8)).pack(anchor="w")

        # ── preview table ─────────────────────────────────────────────
        pvf = ttk.LabelFrame(frm, text="Preview  (click Preview to refresh)", padding=4)
        pvf.grid(row=4, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        cols = ("orig", "new", "status")
        self.tv = ttk.Treeview(pvf, columns=cols, show="headings", height=9)
        self.tv.heading("orig",   text="Original filename")
        self.tv.heading("new",    text="New filename")
        self.tv.heading("status", text="Status")
        self.tv.column("orig",   width=340, stretch=True)
        self.tv.column("new",    width=340, stretch=True)
        self.tv.column("status", width=100, stretch=False, anchor="center")
        pvsb = ttk.Scrollbar(pvf, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=pvsb.set)
        pvsb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.tag_configure("ok",       foreground="#007700")
        self.tv.tag_configure("conflict", foreground="#cc0000")
        self.tv.tag_configure("nochange", foreground="gray")
        self.tv.tag_configure("missing",  foreground="#cc6600")
        self.tv.tag_configure("suspect",  foreground="#cc6600")

        # ── summary + buttons (same row, left vs right) ───────────────
        self.lbl_summary = ttk.Label(frm, text="", foreground="gray")
        self.lbl_summary.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        act = ttk.Frame(frm)
        act.grid(row=5, column=2, columnspan=2, sticky="e", pady=(6, 0))
        ttk.Button(act, text="Cancel", command=self.destroy).pack(side="right")
        self.btn_apply = ttk.Button(act, text="Rename All",
                                    command=self._apply, state="disabled")
        self.btn_apply.pack(side="right", padx=4)

        frm.rowconfigure(1, weight=1)
        frm.rowconfigure(4, weight=2)
        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        if initial_files:
            for f in initial_files:
                if Path(f).suffix.lower() in AUDIO_EXTS:
                    self.listbox.insert("end", f)

    # ── file list helpers ─────────────────────────────────────────────

    def _add_files(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        for f in files:
            self.listbox.insert("end", f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
        self._clear_preview()

    def _add_folder(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        for p in sorted(Path(d).rglob("*")):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                self.listbox.insert("end", str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        self._clear_preview()

    def _remove(self) -> None:
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
        self._clear_preview()

    def _clear(self) -> None:
        self.listbox.delete(0, "end")
        self._tags_cache.clear()
        self._clear_preview()

    def _clear_preview(self) -> None:
        for row in self.tv.get_children():
            self.tv.delete(row)
        self._preview_rows = []
        self.btn_apply.configure(state="disabled")
        self.lbl_summary.configure(text="")

    # ── preview ───────────────────────────────────────────────────────

    def _build_preview(self) -> None:
        files = list(self.listbox.get(0, "end"))
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some files first.")
            return
        pattern = self.var_pattern.get().strip()
        if not pattern:
            messagebox.showwarning("Trader's Little Jedi", "Enter a rename pattern.")
            return

        ffprobe = get_tool("ffprobe").path(self.config_obj)
        if not ffprobe.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                f"ffprobe not found at:\n{ffprobe}\n\n"
                "Install via Tools → Update all CLI tools.",
            )
            return

        self._clear_preview()
        self.lbl_summary.configure(text="Reading tags…")
        self.update_idletasks()

        def _work() -> None:
            rows: list[tuple[str, str, str, str]] = []  # path, orig, new, status
            new_name_count: dict[str, int] = {}
            for path_str in files:
                p = Path(path_str)
                orig = p.name
                if path_str not in self._tags_cache:
                    self._tags_cache[path_str] = tag_io.read_tags(p, ffprobe)
                tags = self._tags_cache[path_str]
                stem = _apply_pattern(pattern, tags)
                new_name = stem + p.suffix.lower()
                new_name_count[new_name] = new_name_count.get(new_name, 0) + 1
                rows.append((path_str, orig, new_name, ""))

            # Detect which tokens are used in the pattern (for suspect-tag check)
            pattern_tokens = {m.group(1).upper() for m in _TOKEN_RE.finditer(pattern)}
            suspect_tokens = pattern_tokens & _MUST_NOT_BE_BARE_INT

            final: list[tuple[str, str, str, str]] = []
            for path_str, orig, new_name, _ in rows:
                if new_name == orig:
                    status = "no change"
                elif new_name_count[new_name] > 1:
                    status = "conflict"
                elif "[no " in new_name:
                    status = "missing tag"
                else:
                    # Check if any text-only field in the pattern is a bare integer
                    tags_for_file = self._tags_cache.get(path_str, {})
                    if any(
                        re.fullmatch(r"\d+", tags_for_file.get(tok, "").strip())
                        for tok in suspect_tokens
                        if tags_for_file.get(tok, "").strip()
                    ):
                        status = "suspect tag"
                    else:
                        status = "OK"
                final.append((path_str, orig, new_name, status))

            self.after(0, lambda: self._show_preview(final))

        threading.Thread(target=_work, daemon=True).start()

    def _show_preview(self, rows: list[tuple[str, str, str, str]]) -> None:
        self._preview_rows = rows
        for row in self.tv.get_children():
            self.tv.delete(row)

        ok = conflict = nochange = missing = suspect = 0
        for _path, orig, new, status in rows:
            if status == "OK":
                tag = "ok";       ok += 1
            elif status == "conflict":
                tag = "conflict"; conflict += 1
            elif status == "no change":
                tag = "nochange"; nochange += 1
            elif status == "suspect tag":
                tag = "suspect";  suspect += 1
            else:
                tag = "missing";  missing += 1
            self.tv.insert("", "end", values=(orig, new, status), tags=(tag,))

        parts: list[str] = []
        if ok:       parts.append(f"{ok} ready to rename")
        if nochange: parts.append(f"{nochange} unchanged")
        if missing:  parts.append(f"{missing} missing tags")
        if suspect:  parts.append(f"{suspect} suspect tags (bare numbers — fix tags first)")
        if conflict: parts.append(f"{conflict} name conflicts")
        self.lbl_summary.configure(text="   ".join(parts))

        self.btn_apply.configure(
            state="normal" if (ok > 0 and conflict == 0 and suspect == 0) else "disabled")

    # ── apply ─────────────────────────────────────────────────────────

    def _apply(self) -> None:
        files = list(self.listbox.get(0, "end"))
        if len(files) != len(self._preview_rows):
            messagebox.showwarning("Trader's Little Jedi",
                                   "File list has changed — click Preview again.")
            return

        errors: list[str] = []
        renamed = 0
        for path_str, (_p, orig, new, status) in zip(files, self._preview_rows):
            if status != "OK":
                continue
            src = Path(path_str)
            dst = src.parent / new
            if dst.exists() and dst.resolve() != src.resolve():
                errors.append(f"{orig}: target already exists")
                continue
            try:
                src.rename(dst)
                renamed += 1
            except OSError as e:
                errors.append(f"{orig}: {e}")

        self.config_obj["batch_rename_pattern"] = self.var_pattern.get().strip()
        self.config_obj.save()

        if errors:
            messagebox.showerror(
                "Trader's Little Jedi",
                f"Renamed {renamed} file(s).  {len(errors)} error(s):\n\n"
                + "\n".join(errors[:10]),
            )
        else:
            messagebox.showinfo("Trader's Little Jedi", f"Successfully renamed {renamed} file(s).")
        self.destroy()
