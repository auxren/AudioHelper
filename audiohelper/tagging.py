"""Metadata tagging dialog — modeled on foo_tradersfriend's field layout.

Reads existing tags via ffprobe; writes via ffmpeg -c copy so it's a fast
metadata-only rewrite. Cover art (JPG/PNG) is embedded as attached_pic.
Includes a setlist.txt generator using the live-show layout convention."""

import io
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Optional

from . import tag_io
from .tools import get_tool


try:
    from PIL import Image, ImageTk
    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False


# Per-track fields (vary per file). Everything else is "common".
PER_TRACK_TAGS = ("TITLE", "TRACKNUMBER", "DISCNUMBER")
# Order matters for the common-fields panel layout.
COMMON_TAG_LAYOUT = [
    ("ARTIST", "Artist"),
    ("ALBUMARTIST", "Album Artist"),
    ("ALBUM", "Album"),
    ("DATE", "Date"),
    ("GENRE", "Genre"),
    ("TRACKTOTAL", "Total Tracks"),
    ("DISCTOTAL", "Total Discs"),
    ("VENUE", "Venue"),
    ("SOURCE", "Source / Lineage"),
    ("PERFORMER", "Performer"),
    ("COMPOSER", "Composer"),
    ("COMMENT", "Comment"),
]


class TagDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Tag audio files")
        self.config_obj = config
        self.runner = runner  # unused; tagging is fast and runs in its own thread
        self.transient(parent)
        self.geometry("1080x740")

        # State
        self.per_file: dict[str, dict[str, str]] = {}
        self.cover_path: Optional[Path] = None
        self._cover_image: Optional["ImageTk.PhotoImage"] = None
        self._current_file: Optional[str] = None
        self._loading_selection = False

        # ----- top toolbar -----
        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Button(top, text="Add files…", command=self._add_files).pack(side="left")
        ttk.Button(top, text="Add folder…", command=self._add_folder).pack(side="left", padx=4)
        ttk.Button(top, text="Remove", command=self._remove).pack(side="left")
        ttk.Button(top, text="Clear", command=self._clear).pack(side="left", padx=4)
        ttk.Button(top, text="↑", width=3, command=self._move_up).pack(side="left", padx=(12, 0))
        ttk.Button(top, text="↓", width=3, command=self._move_down).pack(side="left")
        ttk.Button(top, text="Auto-number tracks", command=self._auto_number).pack(side="left", padx=(12, 4))
        ttk.Button(top, text="Titles from filenames", command=self._auto_titles).pack(side="left")
        ttk.Button(top, text="Reload from files", command=self._reload).pack(side="left", padx=12)

        # ----- main paned area -----
        main = ttk.Frame(self, padding=(8, 6, 8, 0))
        main.pack(fill="both", expand=True)

        # left: file list + per-track panel
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=False)

        ttk.Label(left, text="Files:").pack(anchor="w")
        list_inner = ttk.Frame(left)
        list_inner.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(list_inner, exportselection=False,
                                  activestyle="dotbox", width=44)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_inner, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)

        pt = ttk.LabelFrame(left, text="Per-track (this file)", padding=8)
        pt.pack(fill="x", pady=(8, 0))
        ttk.Label(pt, text="Title:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_title = tk.StringVar()
        ttk.Entry(pt, textvariable=self.var_title, width=34).grid(row=0, column=1, columnspan=3, sticky="ew", padx=6)
        ttk.Label(pt, text="Track #:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_track = tk.StringVar()
        ttk.Entry(pt, textvariable=self.var_track, width=6).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(pt, text="Disc #:").grid(row=1, column=2, sticky="w", padx=(12, 0))
        self.var_disc = tk.StringVar()
        ttk.Entry(pt, textvariable=self.var_disc, width=6).grid(row=1, column=3, sticky="w", padx=6)
        pt.columnconfigure(1, weight=1)
        for v in (self.var_title, self.var_track, self.var_disc):
            v.trace_add("write", self._save_current_per_track)

        # right: common fields + notes + cover
        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        cm = ttk.LabelFrame(right, text="Common fields (applied to every file)", padding=8)
        cm.pack(fill="x")
        self.common_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(COMMON_TAG_LAYOUT):
            r, c = divmod(i, 2)
            ttk.Label(cm, text=f"{label}:").grid(row=r, column=c * 2, sticky="w", pady=2, padx=(0 if c == 0 else 14, 0))
            v = tk.StringVar()
            ttk.Entry(cm, textvariable=v, width=26).grid(row=r, column=c * 2 + 1, sticky="ew", padx=4, pady=2)
            self.common_vars[key] = v
        cm.columnconfigure(1, weight=1)
        cm.columnconfigure(3, weight=1)

        nt = ttk.LabelFrame(right, text="Notes (free-form; embedded as NOTES tag and used in setlist.txt)", padding=8)
        nt.pack(fill="both", expand=True, pady=(8, 0))
        self.txt_notes = tk.Text(nt, height=8, font=("Consolas", 9), wrap="word")
        nsb = ttk.Scrollbar(nt, orient="vertical", command=self.txt_notes.yview)
        self.txt_notes.configure(yscrollcommand=nsb.set)
        nsb.pack(side="right", fill="y")
        self.txt_notes.pack(side="left", fill="both", expand=True)

        cov = ttk.LabelFrame(right, text="Cover art", padding=8)
        cov.pack(fill="x", pady=(8, 0))
        ttk.Button(cov, text="Browse…", command=self._pick_cover).pack(side="left")
        ttk.Button(cov, text="Clear", command=self._clear_cover).pack(side="left", padx=4)
        self.var_cover_action = tk.StringVar(value="keep")
        ttk.Radiobutton(cov, text="Keep existing", variable=self.var_cover_action,
                        value="keep").pack(side="left", padx=(12, 4))
        ttk.Radiobutton(cov, text="Replace", variable=self.var_cover_action,
                        value="replace").pack(side="left", padx=4)
        ttk.Radiobutton(cov, text="Remove embedded", variable=self.var_cover_action,
                        value="remove").pack(side="left", padx=4)
        self.lbl_cover_path = ttk.Label(cov, text="(none)", foreground="gray")
        self.lbl_cover_path.pack(side="left", padx=12)
        self.lbl_cover_img = ttk.Label(cov)
        self.lbl_cover_img.pack(side="left", padx=12)

        # ----- bottom action bar -----
        act = ttk.Frame(self, padding=8)
        act.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(act, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Button(act, text="Generate setlist.txt…",
                   command=self._generate_setlist).pack(side="left", padx=(12, 4))
        self.btn_cancel = ttk.Button(act, text="Close", command=self.destroy)
        self.btn_cancel.pack(side="right")
        self.btn_apply = ttk.Button(act, text="Apply tags", command=self._apply)
        self.btn_apply.pack(side="right", padx=4)

        self.status = ttk.Label(self, text="Ready", anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        if initial_files:
            from .action_picker import AUDIO_EXTS
            for f in initial_files:
                if Path(f).is_file() and Path(f).suffix.lower() in AUDIO_EXTS:
                    self.listbox.insert("end", f)
            if self.listbox.size() > 0:
                self.after(50, self._reload)  # populate tags after layout settles

    # ----- file list -----

    def _add_files(self):
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio files", ("*.flac", "*.mp3", "*.m4a", "*.aac", "*.ogg",
                                 "*.opus", "*.wav", "*.bwf", "*.ape", "*.dff",
                                 "*.dsf", "*.wma", "*.aiff", "*.aif")),
                ("All files", "*.*"),
            ],
        )
        for f in files:
            self.listbox.insert("end", f)
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
            self._reload()

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
                self.listbox.insert("end", str(p))
        self.config_obj["last_input_dir"] = d
        self.config_obj.save()
        self._reload()

    def _remove(self):
        for i in reversed(self.listbox.curselection()):
            path = self.listbox.get(i)
            self.per_file.pop(path, None)
            self.listbox.delete(i)
        self._on_select()

    def _clear(self):
        self.listbox.delete(0, "end")
        self.per_file.clear()
        self._on_select()

    def _move_up(self):
        sel = list(self.listbox.curselection())
        if not sel or sel[0] == 0:
            return
        for i in sel:
            text = self.listbox.get(i)
            self.listbox.delete(i)
            self.listbox.insert(i - 1, text)
        self.listbox.selection_clear(0, "end")
        for i in sel:
            self.listbox.selection_set(i - 1)

    def _move_down(self):
        sel = list(self.listbox.curselection())
        if not sel or sel[-1] == self.listbox.size() - 1:
            return
        for i in reversed(sel):
            text = self.listbox.get(i)
            self.listbox.delete(i)
            self.listbox.insert(i + 1, text)
        self.listbox.selection_clear(0, "end")
        for i in sel:
            self.listbox.selection_set(i + 1)

    # ----- selection -----

    def _on_select(self, _evt=None):
        sel = self.listbox.curselection()
        path = self.listbox.get(sel[0]) if sel else None
        self._current_file = path
        self._loading_selection = True
        try:
            d = self.per_file.get(path or "", {})
            self.var_title.set(d.get("TITLE", ""))
            self.var_track.set(d.get("TRACKNUMBER", ""))
            self.var_disc.set(d.get("DISCNUMBER", ""))
        finally:
            self._loading_selection = False
        if path:
            self.status.configure(text=f"editing: {Path(path).name}")

    def _save_current_per_track(self, *_args):
        if self._loading_selection or not self._current_file:
            return
        d = self.per_file.setdefault(self._current_file, {})
        d["TITLE"] = self.var_title.get()
        d["TRACKNUMBER"] = self.var_track.get()
        d["DISCNUMBER"] = self.var_disc.get()

    # ----- reload tags from files -----

    def _reload(self):
        files = [self.listbox.get(i) for i in range(self.listbox.size())]
        if not files:
            return
        ffprobe = get_tool("ffprobe").path(self.config_obj)
        if not ffprobe.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                "ffprobe.exe not found. Open Tools → Update all CLI tools to install.",
            )
            return
        self.status.configure(text="Reading tags…")
        threading.Thread(target=self._reload_worker, args=(files, ffprobe), daemon=True).start()

    def _reload_worker(self, files: list[str], ffprobe: Path):
        # Sample first file for common defaults; per-file dicts are exact reads
        common_sample: dict[str, str] = {}
        for i, f in enumerate(files):
            tags = tag_io.read_tags(Path(f), ffprobe)
            self.per_file[f] = {
                "TITLE": tags.get("TITLE", ""),
                "TRACKNUMBER": tags.get("TRACKNUMBER", ""),
                "DISCNUMBER": tags.get("DISCNUMBER", ""),
            }
            if i == 0:
                common_sample = tags
        self.after(0, lambda: self._apply_common_sample(common_sample))

    def _apply_common_sample(self, sample: dict[str, str]):
        for key, var in self.common_vars.items():
            if not var.get():  # only populate empty fields
                var.set(sample.get(key, ""))
        if not self.txt_notes.get("1.0", "end").strip() and sample.get("NOTES"):
            self.txt_notes.insert("1.0", sample["NOTES"])
        self._on_select()
        self.status.configure(text=f"Loaded tags from {self.listbox.size()} file(s)")

    # ----- auto helpers -----

    def _auto_number(self):
        files = [self.listbox.get(i) for i in range(self.listbox.size())]
        for i, f in enumerate(files, 1):
            d = self.per_file.setdefault(f, {})
            d["TRACKNUMBER"] = str(i)
        # Set total tracks if blank
        if not self.common_vars["TRACKTOTAL"].get():
            self.common_vars["TRACKTOTAL"].set(str(len(files)))
        self._on_select()
        self.status.configure(text=f"Numbered {len(files)} track(s)")

    def _auto_titles(self):
        files = [self.listbox.get(i) for i in range(self.listbox.size())]
        for f in files:
            stem = Path(f).stem
            # Strip "01 - " / "01. " / "01_" prefixes
            m = re.match(r"^\s*(\d+)\s*[-._\s]+\s*(.+)$", stem)
            if m:
                title = m.group(2).strip()
                track = m.group(1)
            else:
                title = stem.strip()
                track = ""
            # Clean separators -> spaces
            title = re.sub(r"[_\.]+", " ", title)
            title = re.sub(r"\s+", " ", title).strip()
            d = self.per_file.setdefault(f, {})
            d["TITLE"] = title
            if track and not d.get("TRACKNUMBER"):
                d["TRACKNUMBER"] = track
        self._on_select()
        self.status.configure(text=f"Filled titles for {len(files)} file(s)")

    # ----- cover -----

    def _pick_cover(self):
        f = filedialog.askopenfilename(
            parent=self, title="Cover image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp"), ("All files", "*.*")],
        )
        if not f:
            return
        self.cover_path = Path(f)
        self.lbl_cover_path.configure(text=self.cover_path.name)
        self.var_cover_action.set("replace")
        self._render_cover_thumbnail(self.cover_path)

    def _clear_cover(self):
        self.cover_path = None
        self._cover_image = None
        self.lbl_cover_img.configure(image="")
        self.lbl_cover_path.configure(text="(none)")

    def _render_cover_thumbnail(self, path: Path):
        if not _PIL_AVAILABLE:
            self.lbl_cover_img.configure(text="(install Pillow for thumbnail)")
            return
        try:
            im = Image.open(path)
            im.thumbnail((96, 96))
            self._cover_image = ImageTk.PhotoImage(im)
            self.lbl_cover_img.configure(image=self._cover_image, text="")
        except Exception as e:
            self.lbl_cover_img.configure(text=f"(thumb error: {e})")

    # ----- apply -----

    def _apply(self):
        files = [self.listbox.get(i) for i in range(self.listbox.size())]
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some files first.")
            return
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror(
                "Trader's Little Jedi",
                "ffmpeg.exe not found. Open Tools → Update all CLI tools to install.",
            )
            return
        cover_action = self.var_cover_action.get()
        if cover_action == "replace" and not self.cover_path:
            messagebox.showwarning("Trader's Little Jedi",
                                   "Choose a cover image first, or pick 'Keep existing'.")
            return

        common = {k: v.get().strip() for k, v in self.common_vars.items() if v.get().strip()}
        notes_text = self.txt_notes.get("1.0", "end").strip()
        if notes_text:
            common["NOTES"] = notes_text

        self.btn_apply.state(["disabled"])
        self.progress.configure(maximum=len(files), value=0)
        threading.Thread(
            target=self._apply_worker,
            args=(files, common, cover_action, ffmpeg),
            daemon=True,
        ).start()

    def _apply_worker(self, files, common, cover_action, ffmpeg_exe):
        ok = 0
        fail = 0
        for i, f in enumerate(files, 1):
            self._set_status(f"[{i}/{len(files)}] {Path(f).name}")
            per = self.per_file.get(f, {})
            tags = dict(common)
            for k in PER_TRACK_TAGS:
                val = per.get(k, "")
                if val:
                    tags[k] = val
                else:
                    # explicit empty -> remove
                    tags[k] = ""
            try:
                tag_io.apply_tags_in_place(
                    ffmpeg_exe, Path(f), tags,
                    cover=self.cover_path if cover_action == "replace" else None,
                    clear_cover=(cover_action == "remove"),
                )
                ok += 1
            except Exception as e:
                fail += 1
                self._log(f"FAIL {Path(f).name}: {e}")
            self._tick(i)
        self.after(0, lambda: self._finish(ok, fail))

    def _set_status(self, text: str):
        self.after(0, lambda: self.status.configure(text=text))

    def _tick(self, v: int):
        self.after(0, lambda: self.progress.configure(value=v))

    def _log(self, text: str):
        # Echo failures to status (caller of this dialog has the main log pane,
        # but we keep this self-contained).
        self.after(0, lambda: self.status.configure(text=text))

    def _finish(self, ok: int, fail: int):
        self.btn_apply.state(["!disabled"])
        self.status.configure(
            text=f"Done. {ok} tagged, {fail} failed."
        )
        if fail == 0:
            messagebox.showinfo("Trader's Little Jedi", f"Tagged {ok} file(s) successfully.")

    # ----- setlist.txt -----

    def _generate_setlist(self):
        files = [self.listbox.get(i) for i in range(self.listbox.size())]
        if not files:
            messagebox.showwarning("Trader's Little Jedi", "Add some files first.")
            return

        # Build setlist from current dialog state (no need to re-read files)
        artist = self.common_vars["ARTIST"].get().strip()
        date = self.common_vars["DATE"].get().strip()
        venue = self.common_vars["VENUE"].get().strip()
        source = self.common_vars["SOURCE"].get().strip()
        notes = self.txt_notes.get("1.0", "end").strip()

        # Group by disc, then sort by track number within each disc
        by_disc: dict[str, list[tuple[int, str, str]]] = {}
        for f in files:
            per = self.per_file.get(f, {})
            disc = (per.get("DISCNUMBER", "") or "1").strip() or "1"
            try:
                track = int((per.get("TRACKNUMBER", "") or "0").strip())
            except ValueError:
                track = 0
            title = per.get("TITLE", "") or Path(f).stem
            by_disc.setdefault(disc, []).append((track, title, f))

        # Sort discs numerically when possible
        def disc_sort_key(d):
            try:
                return (0, int(d))
            except ValueError:
                return (1, d)
        disc_keys = sorted(by_disc.keys(), key=disc_sort_key)

        lines: list[str] = []
        if artist:
            lines.append(f"Artist: {artist}")
        if date:
            lines.append(f"Date: {date}")
        if venue:
            lines.append(f"Venue: {venue}")
        if source:
            lines.append(f"Source: {source}")
        if lines:
            lines.append("")

        for disc in disc_keys:
            entries = sorted(by_disc[disc], key=lambda e: (e[0] if e[0] else 9999, e[1]))
            if len(disc_keys) > 1 or disc != "1":
                lines.append(f"Disc {disc}")
            for track, title, _path in entries:
                tag = f"t{track:02d}" if track else "t??"
                lines.append(f"{tag} {title}")
            lines.append("")

        if notes:
            lines.append("--Notes--")
            lines.append(notes)
            lines.append("")

        text = "\n".join(lines).rstrip() + "\n"

        # Suggest filename
        first_dir = Path(files[0]).parent
        base = first_dir.name or "setlist"
        suggested = base + ".txt"

        out = filedialog.asksaveasfilename(
            parent=self, title="Save setlist text",
            initialdir=str(first_dir), initialfile=suggested,
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("All files", "*.*")],
        )
        if not out:
            return
        try:
            Path(out).write_text(text, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Trader's Little Jedi", f"Could not write file:\n{e}")
            return
        self.status.configure(text=f"Wrote setlist: {out}")
