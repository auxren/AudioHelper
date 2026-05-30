"""Live Show Tagger — unified metadata editor combining TagDialog and LiveTaggerDialog.

Provides a single window for:
  - Batch audio file tagging (common fields, per-track fields, cover art)
  - eTree/furthur-style text file loading and setlist matching
  - Track title revert from setlist, cover art management per type
  - Strip-first option before applying tags
  - Generate eTree text file from current state
"""

import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import tag_io
from .action_picker import AUDIO_EXTS
from .live_tagger import (
    EtreeShow,
    _IGNORE_PATTERNS,
    _should_ignore,
    generate_etree_file,
    parse_etree_file,
    read_text_smart,
)
from .tc_sources import detect_source
from .tc_tagger import mutagen_available, write_tags as mutagen_write_tags
from .tools import get_tool

try:
    from PIL import Image, ImageTk
    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False


# ── Tag layout constants ──────────────────────────────────────────────────────

# Common fields: (tag_key, display_label, is_multiline)
COMMON_TAG_LAYOUT = [
    ("ARTIST",      "Artist",          False),
    ("ALBUMARTIST", "Album Artist",     False),
    ("ALBUM",       "Album",            False),
    ("DATE",        "Date",             False),
    ("VENUE",       "Venue",            False),
    ("LOCATION",    "Location",         False),
    ("GENRE",       "Genre",            False),
    ("TRACKTOTAL",  "Total Tracks",     False),
    ("DISCTOTAL",   "Total Discs",      False),
    ("SOURCE",      "Source / Lineage", True),   # multiline Text widget
    ("PERFORMER",   "Performer",        False),
    ("COMPOSER",    "Composer",         False),
    ("COMMENT",     "Comment",          False),
]

PER_TRACK_TAGS = ("TITLE", "TRACKNUMBER", "DISCNUMBER")

# Cover art types: (picture_type_int, label, metaflac_type_str)
COVER_TYPES = [
    (3, "Front Cover",       "3"),
    (4, "Back Cover",        "4"),
    (5, "Inside / Leaflet",  "5"),
    (6, "Media / CD",        "6"),
    (0, "Other",             "0"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_quiet(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        args,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    stderr = (r.stderr or b"").decode("utf-8", errors="replace")
    return r.returncode, stderr


def _strip_flac_tags(path: Path, metaflac: Path) -> None:
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


def _strip_ffmpeg_tags(path: Path, ffmpeg: Path) -> None:
    tmp = path.with_name(path.stem + ".jedistriptmp" + path.suffix)
    rc, err = _run_quiet([
        str(ffmpeg), "-y", "-hide_banner", "-i", str(path),
        "-c", "copy", "-map_metadata", "-1",
        str(tmp),
    ])
    if rc != 0:
        tmp.unlink(missing_ok=True)
        detail = err.strip().splitlines()[-1] if err.strip() else ""
        raise RuntimeError(f"ffmpeg exited {rc}" + (f": {detail}" if detail else ""))
    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg produced no output")
    os.replace(tmp, path)


def _auto_title_from_stem(stem: str) -> tuple[str, str]:
    """Return (title, track_number) extracted from a filename stem."""
    m = re.match(r"^\s*(\d+)\s*[-._\s]+\s*(.+)$", stem)
    if m:
        title = m.group(2).strip()
        track = m.group(1)
    else:
        title = stem.strip()
        track = ""
    title = re.sub(r"[_\.]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title, track


def _set_number_for_track(show: EtreeShow, global_index: int) -> int:
    for i, s in enumerate(show.sets, 1):
        for t in s.tracks:
            if t.global_index == global_index:
                return i
    return 1


# ── Main dialog ───────────────────────────────────────────────────────────────

class JediTaggerDialog(tk.Toplevel):
    _open_instance: "JediTaggerDialog | None" = None

    @classmethod
    def open_or_add(cls, parent, config, runner, initial_files=None):
        """Return existing open instance (adding files) or create a new one."""
        inst = cls._open_instance
        if inst is not None:
            try:
                if inst.winfo_exists():
                    if initial_files:
                        inst._add_files_list(initial_files)
                    inst.lift()
                    inst.focus_force()
                    return inst
            except Exception:
                pass
        return cls(parent, config, runner, initial_files)

    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        JediTaggerDialog._open_instance = self
        self.bind("<Destroy>", lambda _e: self._on_destroy())

        self.title("Live Show Tagger")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("1160x760")
        self.minsize(900, 580)

        # ── State ────────────────────────────────────────────────────────────
        self._audio_files: list[str] = []          # ordered list of file paths
        self._per_file: dict[str, dict[str, str]] = {}  # per-file tag overrides
        self._current_file: Optional[str] = None
        self._loading_selection = False

        self._show: Optional[EtreeShow] = None       # parsed from text file
        self._orig_show: Optional[EtreeShow] = None  # pristine copy for revert
        self._txt_path: Optional[Path] = None

        # Cover paths per type_int → Path
        self._cover_paths: dict[int, Optional[Path]] = {t: None for t, _, _ in COVER_TYPES}
        self._cover_images: dict[int, Optional["ImageTk.PhotoImage"]] = {
            t: None for t, _, _ in COVER_TYPES
        }

        # ── Build UI ─────────────────────────────────────────────────────────
        self._build_toolbar()
        self._build_bottom_bar()   # packed BEFORE body so it's always visible
        self._build_body()
        self._build_status_bar()

        if initial_files:
            self.after(50, lambda: self._add_files_list(initial_files))

    # ── Singleton cleanup ─────────────────────────────────────────────────────

    def _on_destroy(self) -> None:
        if JediTaggerDialog._open_instance is self:
            JediTaggerDialog._open_instance = None

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 0))
        bar.pack(fill="x")

        ttk.Button(bar, text="Add files…",   command=self._add_files_dialog).pack(side="left")
        ttk.Button(bar, text="Add folder…",  command=self._add_folder_dialog).pack(side="left", padx=4)
        ttk.Button(bar, text="Remove",       command=self._remove_selected).pack(side="left")
        ttk.Button(bar, text="Clear",        command=self._clear_all_files).pack(side="left", padx=4)
        ttk.Button(bar, text="↑", width=3,   command=self._move_up).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="↓", width=3,   command=self._move_down).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(bar, text="Auto-number",        command=self._auto_number).pack(side="left")
        ttk.Button(bar, text="Titles from filenames", command=self._titles_from_filenames).pack(side="left", padx=4)
        ttk.Button(bar, text="Reload tags",        command=self._reload_tags_from_files).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

        ttk.Button(bar, text="Load text file…", command=self._load_txt_dialog).pack(side="left")
        ttk.Button(bar, text="Reload text",      command=self._reload_txt).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear text",       command=self._clear_txt).pack(side="left")

    # ── Bottom bar (always visible, packed before body) ───────────────────────

    def _build_bottom_bar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x", side="bottom")

        self.var_strip_first = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Strip existing tags first",
                        variable=self.var_strip_first).pack(side="left")

        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

        ttk.Button(bar, text="Close",             command=self.destroy).pack(side="right")
        self.btn_apply = ttk.Button(bar, text="Apply tags", command=self._apply_tags)
        self.btn_apply.pack(side="right", padx=4)
        ttk.Button(bar, text="Generate text file", command=self._generate_txt).pack(side="right", padx=4)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        self.status = ttk.Label(self, text="Add files to begin.",
                                anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

    # ── Main body ─────────────────────────────────────────────────────────────

    def _build_body(self) -> None:
        body = ttk.Frame(self, padding=(8, 4, 8, 4))
        body.pack(fill="both", expand=True)

        paned = ttk.PanedWindow(body, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # ── LEFT pane: file list + per-track ─────────────────────────────────
        left = ttk.Frame(paned)
        self._build_file_list(left)
        self._build_per_track_section(left)
        paned.add(left, weight=2)

        # ── RIGHT pane: notebook tabs ─────────────────────────────────────────
        right = ttk.Frame(paned, padding=(4, 0, 0, 0))
        self._build_right_notebook(right)
        paned.add(right, weight=3)

    def _build_file_list(self, parent: tk.Widget) -> None:
        lf = ttk.LabelFrame(parent, text="Files", padding=4)
        lf.pack(fill="both", expand=True)

        cols = ("num", "filename", "title", "match")
        # extended = click, shift-click for a range, cmd/ctrl-click for individual
        self.tv = ttk.Treeview(lf, columns=cols, show="headings", selectmode="extended")
        self.tv.heading("num",      text="#",        anchor="center")
        self.tv.heading("filename", text="Filename",  anchor="w")
        self.tv.heading("title",    text="Title",     anchor="w")
        self.tv.heading("match",    text="✓",         anchor="center")
        self.tv.column("num",      width=32,  minwidth=28,  stretch=False, anchor="center")
        self.tv.column("filename", width=190, minwidth=80,  stretch=True,  anchor="w")
        self.tv.column("title",    width=170, minwidth=60,  stretch=True,  anchor="w")
        self.tv.column("match",    width=24,  minwidth=22,  stretch=False, anchor="center")

        tv_sb = ttk.Scrollbar(lf, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=tv_sb.set)
        tv_sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)

        # Green = matched, default = unmatched (gray text), orange = extra file
        self.tv.tag_configure("matched",   foreground="#007700")
        self.tv.tag_configure("unmatched", foreground="#888888")
        self.tv.tag_configure("extra",     foreground="#cc6600")

        self.tv.bind("<<TreeviewSelect>>", self._on_file_select)
        # Select-all: Cmd-A on Mac, Ctrl-A elsewhere
        self.tv.bind("<Command-a>", self._select_all_files)
        self.tv.bind("<Command-A>", self._select_all_files)
        self.tv.bind("<Control-a>", self._select_all_files)
        self.tv.bind("<Control-A>", self._select_all_files)

    def _select_all_files(self, _evt=None) -> str:
        self.tv.selection_set(self.tv.get_children())
        return "break"

    def _build_per_track_section(self, parent: tk.Widget) -> None:
        pt = ttk.LabelFrame(parent, text="Per-track (this file)", padding=8)
        pt.pack(fill="x", pady=(4, 0))
        pt.columnconfigure(1, weight=1)

        ttk.Label(pt, text="Title:").grid(row=0, column=0, sticky="w", pady=2)
        self.var_title = tk.StringVar()
        ttk.Entry(pt, textvariable=self.var_title).grid(
            row=0, column=1, sticky="ew", padx=6, pady=2)
        self.btn_revert_title = ttk.Button(pt, text="Revert", width=6,
                                           command=self._revert_title)
        self.btn_revert_title.grid(row=0, column=2, sticky="w", pady=2)

        ttk.Label(pt, text="Track #:").grid(row=1, column=0, sticky="w", pady=2)
        self.var_track = tk.StringVar()
        ttk.Entry(pt, textvariable=self.var_track, width=8).grid(
            row=1, column=1, sticky="w", padx=6, pady=2)

        ttk.Label(pt, text="Disc #:").grid(row=2, column=0, sticky="w", pady=2)
        self.var_disc = tk.StringVar()
        ttk.Entry(pt, textvariable=self.var_disc, width=8).grid(
            row=2, column=1, sticky="w", padx=6, pady=2)

        for v in (self.var_title, self.var_track, self.var_disc):
            v.trace_add("write", self._save_per_track)

    def _build_right_notebook(self, parent: tk.Widget) -> None:
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)

        # Tab 1: Show Data
        tab_show = ttk.Frame(nb, padding=6)
        nb.add(tab_show, text="Show Data")
        self._build_show_data_tab(tab_show)

        # Tab 2: Cover Art
        tab_cover = ttk.Frame(nb, padding=6)
        nb.add(tab_cover, text="Cover Art")
        self._build_cover_art_tab(tab_cover)

        # Tab 3: Setlist
        tab_setlist = ttk.Frame(nb, padding=6)
        nb.add(tab_setlist, text="Setlist")
        self._build_setlist_tab(tab_setlist)

        # Tab 4: File Tags (read-only inspector for the selected file)
        tab_tags = ttk.Frame(nb, padding=6)
        nb.add(tab_tags, text="File Tags")
        self._build_alltags_tab(tab_tags)

    # Standard tag fields shown in the inspector, in mp3tag order.
    # (key, friendly label)
    _STD_TAG_FIELDS = [
        ("TITLE",       "Title"),
        ("ARTIST",      "Artist"),
        ("ALBUM",       "Album"),
        ("DATE",        "Year"),
        ("TRACKNUMBER", "Track"),
        ("TRACKTOTAL",  "Track Total"),
        ("GENRE",       "Genre"),
        ("COMMENT",     "Comment"),
        ("ALBUMARTIST", "Album Artist"),
        ("COMPOSER",    "Composer"),
        ("DISCNUMBER",  "Disc Number"),
        ("DISCTOTAL",   "Disc Total"),
    ]

    def _build_alltags_tab(self, parent: tk.Widget) -> None:
        top = ttk.Frame(parent)
        top.pack(fill="x", pady=(0, 4))
        self._alltags_file_lbl = ttk.Label(
            top, text="Select a file to see its tags.", foreground="gray")
        self._alltags_file_lbl.pack(side="left")
        ttk.Button(top, text="Refresh",
                   command=self._refresh_alltags).pack(side="right")

        cols = ("tag", "value")
        self.tv_alltags = ttk.Treeview(parent, columns=cols, show="headings",
                                       selectmode="browse")
        self.tv_alltags.heading("tag",   text="Field", anchor="w")
        self.tv_alltags.heading("value", text="Value", anchor="w")
        self.tv_alltags.column("tag",   width=120, minwidth=80,  stretch=False, anchor="w")
        self.tv_alltags.column("value", width=320, minwidth=120, stretch=True,  anchor="w")
        self.tv_alltags.tag_configure("empty", foreground="#888888")
        at_sb = ttk.Scrollbar(parent, orient="vertical", command=self.tv_alltags.yview)
        self.tv_alltags.configure(yscrollcommand=at_sb.set)
        at_sb.pack(side="right", fill="y")
        self.tv_alltags.pack(fill="both", expand=True)

        info = ttk.Label(
            parent,
            text="The standard tags on the selected file (read with ffprobe).\n"
                 "Empty fields and untagged files are shown so you can see what's missing.",
            foreground="gray", justify="left")
        info.pack(anchor="w", pady=(4, 0))

    def _build_show_data_tab(self, parent: tk.Widget) -> None:
        # Text file status row
        txt_row = ttk.Frame(parent)
        txt_row.pack(fill="x", pady=(0, 6))
        ttk.Label(txt_row, text="Text file:").pack(side="left")
        self.lbl_txt = ttk.Label(txt_row, text="[none loaded]", foreground="gray",
                                 relief="sunken", padding=(4, 1))
        self.lbl_txt.pack(side="left", padx=6, fill="x", expand=True)
        ttk.Button(txt_row, text="Load…",   command=self._load_txt_dialog).pack(side="left")
        ttk.Button(txt_row, text="Reload",  command=self._reload_txt,      ).pack(side="left", padx=2)
        ttk.Button(txt_row, text="Clear",   command=self._clear_txt,       ).pack(side="left", padx=2)

        # Common fields in a scrollable frame with 2-column grid where possible
        fields_lf = ttk.LabelFrame(parent, text="Common fields (applied to every file)", padding=8)
        fields_lf.pack(fill="both", expand=True)
        fields_lf.columnconfigure(1, weight=1)
        fields_lf.columnconfigure(4, weight=1)

        self._common_vars: dict[str, tk.StringVar] = {}
        self._source_widget: Optional[tk.Text] = None
        self._revert_btns: dict[str, ttk.Button] = {}

        # Layout: two columns of fields, SOURCE spans full width as multiline
        row_i = 0
        left_fields = []
        right_fields = []
        source_field = None
        other_fields = []

        for key, label, is_multi in COMMON_TAG_LAYOUT:
            if is_multi:
                source_field = (key, label)
            else:
                other_fields.append((key, label))

        # Split non-multiline fields into two columns
        half = (len(other_fields) + 1) // 2
        left_fields = other_fields[:half]
        right_fields = other_fields[half:]

        def _make_entry(parent_grid, key, label, row, col_offset):
            ttk.Label(parent_grid, text=f"{label}:").grid(
                row=row, column=col_offset,     sticky="w", pady=2,
                padx=(0 if col_offset == 0 else 16, 0))
            var = tk.StringVar()
            self._common_vars[key] = var
            ttk.Entry(parent_grid, textvariable=var).grid(
                row=row, column=col_offset + 1, sticky="ew", padx=(4, 0), pady=2)
            btn = ttk.Button(parent_grid, text="↩", width=3,
                             command=lambda k=key: self._revert_common_field(k))
            btn.grid(row=row, column=col_offset + 2, sticky="w", padx=(2, 8), pady=2)
            self._revert_btns[key] = btn

        for i, (key, label) in enumerate(left_fields):
            _make_entry(fields_lf, key, label, i, 0)
            row_i = max(row_i, i + 1)

        for i, (key, label) in enumerate(right_fields):
            _make_entry(fields_lf, key, label, i, 3)

        # SOURCE field: full-width multiline
        if source_field:
            key, label = source_field
            ttk.Label(fields_lf, text=f"{label}:").grid(
                row=row_i, column=0, sticky="nw", pady=(6, 2))
            self._source_widget = tk.Text(fields_lf, height=3,
                                          font=("Segoe UI", 9), wrap="word")
            self._source_widget.grid(row=row_i, column=1, columnspan=4,
                                     sticky="ew", padx=(4, 0), pady=(6, 2))
            btn = ttk.Button(fields_lf, text="↩", width=3,
                             command=lambda k=key: self._revert_common_field(k))
            btn.grid(row=row_i, column=5, sticky="nw", padx=(2, 0), pady=(6, 2))
            self._revert_btns[key] = btn
            row_i += 1

        # Notes area
        notes_lf = ttk.LabelFrame(parent, text="Notes", padding=4)
        notes_lf.pack(fill="x", pady=(6, 0))
        self.txt_notes = tk.Text(notes_lf, height=4, font=("Consolas", 9), wrap="word")
        notes_sb = ttk.Scrollbar(notes_lf, orient="vertical", command=self.txt_notes.yview)
        self.txt_notes.configure(yscrollcommand=notes_sb.set)
        notes_sb.pack(side="right", fill="y")
        self.txt_notes.pack(fill="both", expand=True)

    def _build_cover_art_tab(self, parent: tk.Widget) -> None:
        # ── Embedded cover preview (read from the loaded files) ──────────────
        self._embedded_cover_img = None  # keep a ref so it isn't GC'd
        emb = ttk.LabelFrame(parent, text="Embedded cover (from files)", padding=6)
        emb.pack(fill="x", pady=(0, 6))
        self._emb_thumb = ttk.Label(emb, text="(scanning…)", foreground="gray",
                                    width=12, anchor="center")
        self._emb_thumb.pack(side="left", padx=(0, 8))
        self._emb_info = ttk.Label(emb, text="No files loaded yet.",
                                   foreground="gray", justify="left")
        self._emb_info.pack(side="left", fill="x", expand=True)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=(0, 6))
        ttk.Label(parent, text="Add or replace cover art:",
                  foreground="gray").pack(anchor="w", pady=(0, 2))

        # One row per cover type
        self._cover_path_labels: dict[int, ttk.Label] = {}
        self._cover_thumb_labels: dict[int, ttk.Label] = {}

        for pic_type, label, _ in COVER_TYPES:
            row_frame = ttk.LabelFrame(parent, text=label, padding=6)
            row_frame.pack(fill="x", pady=2)

            thumb_lbl = ttk.Label(row_frame, text="(none)", foreground="gray", width=10)
            thumb_lbl.pack(side="left", padx=(0, 8))
            self._cover_thumb_labels[pic_type] = thumb_lbl

            path_lbl = ttk.Label(row_frame, text="(not selected)", foreground="gray",
                                 relief="sunken", padding=(4, 1))
            path_lbl.pack(side="left", fill="x", expand=True)
            self._cover_path_labels[pic_type] = path_lbl

            ttk.Button(row_frame, text="Browse…",
                       command=lambda t=pic_type: self._browse_cover(t)).pack(side="left", padx=(6, 2))
            ttk.Button(row_frame, text="Clear",
                       command=lambda t=pic_type: self._clear_cover(t)).pack(side="left")

        # Radio group for cover action
        action_frame = ttk.LabelFrame(parent, text="Cover art action", padding=8)
        action_frame.pack(fill="x", pady=(8, 0))
        self.var_cover_action = tk.StringVar(value="keep")
        ttk.Radiobutton(action_frame, text="Keep existing covers",
                        variable=self.var_cover_action, value="keep").pack(anchor="w")
        ttk.Radiobutton(action_frame, text="Replace with selected",
                        variable=self.var_cover_action, value="replace").pack(anchor="w")
        ttk.Radiobutton(action_frame, text="Remove all covers",
                        variable=self.var_cover_action, value="remove").pack(anchor="w")

    def _build_setlist_tab(self, parent: tk.Widget) -> None:
        self.txt_setlist = tk.Text(parent, font=("Consolas", 9), wrap="none",
                                   state="disabled", background="#f8f8f8")
        sl_sby = ttk.Scrollbar(parent, orient="vertical",   command=self.txt_setlist.yview)
        sl_sbx = ttk.Scrollbar(parent, orient="horizontal", command=self.txt_setlist.xview)
        self.txt_setlist.configure(yscrollcommand=sl_sby.set, xscrollcommand=sl_sbx.set)
        sl_sby.pack(side="right", fill="y")
        sl_sbx.pack(side="bottom", fill="x")
        self.txt_setlist.pack(fill="both", expand=True)
        self.txt_setlist.tag_configure("set_label", font=("Consolas", 9, "bold"))

    # ── File list management ──────────────────────────────────────────────────

    def _add_files_dialog(self) -> None:
        files = filedialog.askopenfilenames(
            parent=self, title="Select audio files",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[
                ("Audio files", tuple(f"*{ext}" for ext in sorted(AUDIO_EXTS))),
                ("All files", "*.*"),
            ],
        )
        if files:
            self.config_obj["last_input_dir"] = str(Path(files[0]).parent)
            self.config_obj.save()
            self._add_files_list(list(files))

    def _add_folder_dialog(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select folder",
            initialdir=self.config_obj.get("last_input_dir") or None,
        )
        if not d:
            return
        folder = Path(d)
        # Recurse into subfolders so multi-disc shows nested in Disc 1/Disc 2/
        # (or any deeper structure) are all picked up.
        files = sorted(
            str(p) for p in folder.rglob("*")
            if p.is_file()
            and p.suffix.lower() in AUDIO_EXTS
            and not _should_ignore(p.name)
        )
        if not files:
            messagebox.showwarning("Live Show Tagger",
                                   f"No supported audio files found in:\n{folder}\n"
                                   "(searched all subfolders)")
            return
        self.config_obj["last_input_dir"] = str(folder)
        self.config_obj.save()
        self._add_files_list(files)

        # Auto-detect a text file anywhere in the tree if not already loaded
        if self._txt_path is None or self._txt_path.parent.resolve() != folder.resolve():
            txts = sorted(
                p for p in folder.rglob("*.txt")
                if not _should_ignore(p.name)
            )
            if txts:
                self._load_txt_path(txts[0])

    def _add_files_list(self, files: list[str]) -> None:
        added = 0
        for f in files:
            p = Path(f)
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS and f not in self._audio_files:
                self._audio_files.append(f)
                added += 1

        if added:
            # Auto-detect txt in common parent if not yet loaded
            if self._txt_path is None:
                parents = {Path(f).parent for f in self._audio_files}
                if len(parents) == 1:
                    folder = parents.pop()
                    txts = sorted(
                        p for p in folder.iterdir()
                        if p.suffix.lower() == ".txt" and not _should_ignore(p.name)
                    )
                    if txts:
                        self._load_txt_path(txts[0])

            self._refresh_file_list()
            self._reload_tags_for_new_files([f for f in files if f in self._audio_files])

    def _remove_selected(self) -> None:
        sel = self.tv.selection()
        if not sel:
            return
        for iid in sel:
            idx = self._iid_to_index(iid)
            if idx is not None:
                path = self._audio_files[idx]
                self._per_file.pop(path, None)
                self._audio_files.pop(idx)
        self._current_file = None
        self._refresh_file_list()
        self._clear_per_track_fields()

    def _clear_all_files(self) -> None:
        self._audio_files.clear()
        self._per_file.clear()
        self._current_file = None
        self._refresh_file_list()
        self._clear_per_track_fields()

    def _move_up(self) -> None:
        sel = self.tv.selection()
        if not sel:
            return
        idx = self._iid_to_index(sel[0])
        if idx is None or idx == 0:
            return
        self._audio_files[idx - 1], self._audio_files[idx] = (
            self._audio_files[idx], self._audio_files[idx - 1])
        self._refresh_file_list()
        # Reselect moved item
        children = self.tv.get_children()
        if idx - 1 < len(children):
            self.tv.selection_set(children[idx - 1])

    def _move_down(self) -> None:
        sel = self.tv.selection()
        if not sel:
            return
        idx = self._iid_to_index(sel[0])
        if idx is None or idx >= len(self._audio_files) - 1:
            return
        self._audio_files[idx], self._audio_files[idx + 1] = (
            self._audio_files[idx + 1], self._audio_files[idx])
        self._refresh_file_list()
        children = self.tv.get_children()
        if idx + 1 < len(children):
            self.tv.selection_set(children[idx + 1])

    def _iid_to_index(self, iid: str) -> Optional[int]:
        children = self.tv.get_children()
        try:
            return list(children).index(iid)
        except ValueError:
            return None

    # ── File selection ────────────────────────────────────────────────────────

    def _on_file_select(self, _evt=None) -> None:
        sel = self.tv.selection()
        if not sel:
            self._current_file = None
            self._clear_per_track_fields()
            return
        idx = self._iid_to_index(sel[0])
        if idx is None or idx >= len(self._audio_files):
            return
        path = self._audio_files[idx]
        self._current_file = path
        self._loading_selection = True
        try:
            d = self._per_file.get(path, {})
            self.var_title.set(d.get("TITLE", ""))
            self.var_track.set(d.get("TRACKNUMBER", ""))
            self.var_disc.set(d.get("DISCNUMBER", ""))
        finally:
            self._loading_selection = False
        self.status.configure(text=f"Editing: {Path(path).name}")
        self._refresh_alltags()

    def _refresh_alltags(self) -> None:
        """Show the standard mp3tag-style fields for the current file."""
        if not hasattr(self, "tv_alltags"):
            return
        for iid in self.tv_alltags.get_children():
            self.tv_alltags.delete(iid)
        path = self._current_file
        if not path:
            self._alltags_file_lbl.configure(text="Select a file to see its tags.")
            return
        self._alltags_file_lbl.configure(text=Path(path).name)

        # Prefer the cached orig tags; fall back to a fresh ffprobe read.
        tags = self._per_file.get(path + "__orig__")
        if tags is None:
            ffprobe = get_tool("ffprobe").path(self.config_obj)
            tags = tag_io.read_tags(Path(path), ffprobe) if ffprobe.exists() else {}

        any_value = False
        for key, label in self._STD_TAG_FIELDS:
            val = str(tags.get(key, "")).strip() if tags else ""
            if val:
                any_value = True
                if len(val) > 200:
                    val = val[:200] + "…"
                self.tv_alltags.insert("", "end", values=(label, val))
            else:
                self.tv_alltags.insert("", "end", values=(label, "—"),
                                       tags=("empty",))
        if not any_value:
            self._alltags_file_lbl.configure(
                text=f"{Path(path).name}  —  no standard tags (untagged file)")

    def _save_per_track(self, *_args) -> None:
        if self._loading_selection or not self._current_file:
            return
        d = self._per_file.setdefault(self._current_file, {})
        d["TITLE"]       = self.var_title.get()
        d["TRACKNUMBER"] = self.var_track.get()
        d["DISCNUMBER"]  = self.var_disc.get()
        # Update title shown in treeview
        idx = self._audio_files.index(self._current_file) if self._current_file in self._audio_files else -1
        if idx >= 0:
            children = self.tv.get_children()
            if idx < len(children):
                iid = children[idx]
                vals = list(self.tv.item(iid, "values"))
                vals[2] = d["TITLE"]
                self.tv.item(iid, values=vals)

    def _clear_per_track_fields(self) -> None:
        self._loading_selection = True
        try:
            self.var_title.set("")
            self.var_track.set("")
            self.var_disc.set("")
        finally:
            self._loading_selection = False

    def _revert_title(self) -> None:
        if not self._current_file:
            return
        # Try to revert to setlist title for this file's position
        if self._show:
            idx = self._audio_files.index(self._current_file) if self._current_file in self._audio_files else -1
            tracks = self._show.all_tracks()
            if 0 <= idx < len(tracks):
                self._loading_selection = True
                try:
                    self.var_title.set(tracks[idx].title)
                finally:
                    self._loading_selection = False
                d = self._per_file.setdefault(self._current_file, {})
                d["TITLE"] = tracks[idx].title
                # Update treeview
                children = self.tv.get_children()
                if idx < len(children):
                    vals = list(self.tv.item(children[idx], "values"))
                    vals[2] = tracks[idx].title
                    self.tv.item(children[idx], values=vals)
                return
        # Fall back to original file tag value
        orig = tag_io.read_tags.__doc__  # just a sentinel; we use stored orig tags
        orig_tags = self._per_file.get(self._current_file + "__orig__", {})
        orig_title = orig_tags.get("TITLE", "")
        self._loading_selection = True
        try:
            self.var_title.set(orig_title)
        finally:
            self._loading_selection = False

    # ── Tag loading ───────────────────────────────────────────────────────────

    def _reload_tags_from_files(self) -> None:
        if not self._audio_files:
            return
        ffprobe = get_tool("ffprobe").path(self.config_obj)
        if not ffprobe.exists():
            messagebox.showerror("Live Show Tagger",
                                 "ffprobe not found. Open Tools → Update all CLI tools.")
            return
        self.status.configure(text="Reading tags…")
        threading.Thread(target=self._reload_worker,
                         args=(list(self._audio_files), ffprobe), daemon=True).start()

    def _reload_tags_for_new_files(self, files: list[str]) -> None:
        if not files:
            return
        ffprobe = get_tool("ffprobe").path(self.config_obj)
        if not ffprobe.exists():
            return
        threading.Thread(target=self._reload_worker,
                         args=(files, ffprobe, True), daemon=True).start()

    def _reload_worker(self, files: list[str], ffprobe: Path,
                       new_only: bool = False) -> None:
        common_sample: dict[str, str] = {}
        for i, f in enumerate(files):
            tags = tag_io.read_tags(Path(f), ffprobe)
            # Store original tag values for revert
            self._per_file[f + "__orig__"] = dict(tags)
            if not new_only or f not in self._per_file:
                self._per_file[f] = {
                    "TITLE":       tags.get("TITLE", ""),
                    "TRACKNUMBER": tags.get("TRACKNUMBER", ""),
                    "DISCNUMBER":  tags.get("DISCNUMBER", ""),
                }
            # Capture the first file's tags as the sample for common fields.
            # _apply_common_sample only fills *empty* fields, so this never
            # clobbers user edits or values loaded from a setlist text file —
            # it just surfaces embedded tags (ALBUM, ARTIST, COMMENT, etc.)
            # that the file already carries. Previously this only ran on the
            # manual "Reload tags" button, so adding a folder left the Show
            # Data fields blank even when the files were fully tagged.
            if i == 0:
                common_sample = tags
        if common_sample:
            self.after(0, lambda: self._apply_common_sample(common_sample))
        else:
            self.after(0, lambda: self._on_file_select())
        # Read & display the embedded cover from the first file, if any.
        if files:
            self.after(0, lambda: self._load_embedded_cover(Path(files[0])))

    def _apply_common_sample(self, sample: dict[str, str]) -> None:
        for key, var in self._common_vars.items():
            if not var.get():
                var.set(sample.get(key, ""))
        if self._source_widget and not self._source_widget.get("1.0", "end").strip():
            src = sample.get("SOURCE", "")
            if src:
                self._source_widget.insert("1.0", src)
        if not self.txt_notes.get("1.0", "end").strip():
            notes = sample.get("NOTES", "")
            if notes:
                self.txt_notes.insert("1.0", notes)
        self._refresh_file_list()
        self._on_file_select()
        self.status.configure(text=f"Loaded tags from {len(self._audio_files)} file(s)")

    # ── Refresh file list view ────────────────────────────────────────────────

    def _refresh_file_list(self) -> None:
        # Remember current selection
        sel = self.tv.selection()
        sel_idx = self._iid_to_index(sel[0]) if sel else None

        for iid in self.tv.get_children():
            self.tv.delete(iid)

        tracks = self._show.all_tracks() if self._show else []
        n_files  = len(self._audio_files)
        n_tracks = len(tracks)
        matched  = min(n_files, n_tracks)

        for i, f in enumerate(self._audio_files):
            per = self._per_file.get(f, {})
            if i < n_tracks:
                t = tracks[i]
                # Show setlist title if per-file title is empty or matches setlist
                title = per.get("TITLE", "") or t.title
                mark = "✓"
                tag_name = "matched"
            else:
                title = per.get("TITLE", "")
                mark = "?" if n_tracks else ""
                tag_name = "extra" if n_tracks else "unmatched"
            self.tv.insert("", "end",
                           values=(i + 1, Path(f).name, title, mark),
                           tags=(tag_name,))

        # Restore selection
        if sel_idx is not None:
            children = self.tv.get_children()
            new_idx = min(sel_idx, len(children) - 1)
            if new_idx >= 0:
                self.tv.selection_set(children[new_idx])

        # Update status summary
        parts: list[str] = []
        if n_files:
            parts.append(f"{n_files} file(s)")
        if n_tracks:
            parts.append(f"{n_tracks} track(s) in setlist")
        if n_files and n_tracks:
            parts.append(f"{matched} matched")
            if n_files > n_tracks:
                parts.append(f"{n_files - n_tracks} extra file(s)")
            elif n_tracks > n_files:
                parts.append(f"{n_tracks - n_files} unmatched track(s)")
        if not parts:
            self.status.configure(text="Add files to begin.")
        else:
            self.status.configure(text="   •   ".join(parts))

    # ── Text file loading ─────────────────────────────────────────────────────

    def _load_txt_dialog(self) -> None:
        f = filedialog.askopenfilename(
            parent=self, title="Select eTree text file",
            initialdir=self.config_obj.get("last_input_dir") or None,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if f:
            self._load_txt_path(Path(f))

    def _load_txt_path(self, p: Path) -> None:
        try:
            content = read_text_smart(p)
        except OSError as e:
            messagebox.showerror("Live Show Tagger", f"Could not read {p.name}:\n{e}")
            return
        self._txt_path = p
        self._show = parse_etree_file(content)
        self._orig_show = parse_etree_file(content)
        self.lbl_txt.configure(text=p.name, foreground="black")
        self._populate_show_fields()
        self._refresh_setlist_view()
        self._refresh_file_list()
        self.status.configure(text=f"Loaded text file: {p.name}")

    def _reload_txt(self) -> None:
        if self._txt_path and self._txt_path.exists():
            self._load_txt_path(self._txt_path)
        else:
            self.status.configure(text="No text file loaded.")

    def _clear_txt(self) -> None:
        self._show = None
        self._orig_show = None
        self._txt_path = None
        self.lbl_txt.configure(text="[none loaded]", foreground="gray")
        self._refresh_setlist_view()
        self._refresh_file_list()
        self.status.configure(text="Text file cleared.")

    def _populate_show_fields(self) -> None:
        if not self._show:
            return
        field_map = {
            "ARTIST":   self._show.artist,
            "DATE":     self._show.date,
            "VENUE":    self._show.venue,
            "LOCATION": self._show.location,
        }
        for key, val in field_map.items():
            if key in self._common_vars:
                self._common_vars[key].set(val)
        if self._source_widget:
            self._source_widget.delete("1.0", "end")
            self._source_widget.insert("1.0", self._show.source)
        if self._show.notes:
            self.txt_notes.delete("1.0", "end")
            self.txt_notes.insert("1.0", self._show.notes)
        # Auto-detect source kind/mics and append label if source field has content
        if self._show.source:
            src_info = detect_source(self._show.source)
            if src_info.label() and "SOURCE" in self._common_vars:
                self._common_vars["SOURCE"].set(self._show.source)

    def _revert_common_field(self, key: str) -> None:
        if not self._orig_show:
            return
        # Map tag key to show attribute
        attr_map = {
            "ARTIST":   "artist",
            "DATE":     "date",
            "VENUE":    "venue",
            "LOCATION": "location",
            "SOURCE":   "source",
        }
        attr = attr_map.get(key)
        if attr is None:
            return
        val = getattr(self._orig_show, attr, "")
        if key == "SOURCE":
            if self._source_widget:
                self._source_widget.delete("1.0", "end")
                self._source_widget.insert("1.0", val)
        else:
            if key in self._common_vars:
                self._common_vars[key].set(val)

    def _get_source(self) -> str:
        if self._source_widget:
            return self._source_widget.get("1.0", "end").strip()
        return ""

    # ── Setlist view ──────────────────────────────────────────────────────────

    def _refresh_setlist_view(self) -> None:
        self.txt_setlist.configure(state="normal")
        self.txt_setlist.delete("1.0", "end")
        if self._show:
            for s in self._show.sets:
                self.txt_setlist.insert("end", f"{s.label}:\n", "set_label")
                for t in s.tracks:
                    suffix = f"  ({t.comment})" if t.comment else ""
                    self.txt_setlist.insert(
                        "end", f"  {t.global_index:2d}.  {t.title}{suffix}\n")
                self.txt_setlist.insert("end", "\n")
        self.txt_setlist.configure(state="disabled")

    # ── Cover art management ──────────────────────────────────────────────────

    def _browse_cover(self, pic_type: int) -> None:
        f = filedialog.askopenfilename(
            parent=self, title=f"Select cover image (type {pic_type})",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp"),
                       ("All files", "*.*")],
        )
        if not f:
            return
        self._cover_paths[pic_type] = Path(f)
        self._cover_path_labels[pic_type].configure(
            text=Path(f).name, foreground="black")
        self.var_cover_action.set("replace")
        self._render_cover_thumb(pic_type, Path(f))

    def _clear_cover(self, pic_type: int) -> None:
        self._cover_paths[pic_type] = None
        self._cover_images[pic_type] = None
        self._cover_path_labels[pic_type].configure(text="(not selected)", foreground="gray")
        lbl = self._cover_thumb_labels.get(pic_type)
        if lbl:
            lbl.configure(image="", text="(none)")

    def _render_cover_thumb(self, pic_type: int, path: Path) -> None:
        lbl = self._cover_thumb_labels.get(pic_type)
        if not lbl:
            return
        if not _PIL_AVAILABLE:
            lbl.configure(text="(PIL missing)", image="")
            return
        try:
            im = Image.open(path)
            im.thumbnail((64, 64))
            photo = ImageTk.PhotoImage(im)
            self._cover_images[pic_type] = photo
            lbl.configure(image=photo, text="")
        except Exception as e:
            lbl.configure(text=f"(err: {e})", image="")

    def _load_embedded_cover(self, audio_path: Path) -> None:
        """Extract and preview the embedded cover from *audio_path*, if present.

        Works for FLAC PICTURE blocks (even when ffprobe doesn't flag the
        stream as attached_pic — XLD-tagged files do this), MP3 APIC, M4A
        cover atoms, etc. Uses ffmpeg to dump the first video/image stream.
        """
        if not hasattr(self, "_emb_thumb"):
            return
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            self._emb_info.configure(text="ffmpeg not found — cannot read cover.")
            return
        self._emb_thumb.configure(text="(scanning…)", image="")
        self._emb_info.configure(text=f"Reading cover from {audio_path.name}…")

        def _worker():
            import tempfile
            tmp = Path(tempfile.gettempdir()) / "_tlj_embcover.jpg"
            # -map 0:v? grabs any embedded picture/video stream (the cover).
            rc = subprocess.run(
                [str(ffmpeg), "-y", "-hide_banner", "-i", str(audio_path),
                 "-map", "0:v:0", "-frames:v", "1", str(tmp)],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            ok = rc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0
            self.after(0, lambda: self._show_embedded_cover(ok, tmp))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_embedded_cover(self, ok: bool, tmp: Path) -> None:
        if not ok:
            self._emb_thumb.configure(text="(none)", image="")
            self._emb_info.configure(
                text="No embedded cover found in these files.")
            return
        # We have a cover — wire it into the Front Cover slot so it's preserved
        # on save, and show a preview.
        if not _PIL_AVAILABLE:
            self._emb_thumb.configure(text="✓ has\ncover", image="")
            self._emb_info.configure(
                text="Embedded cover detected (install Pillow to preview).\n"
                     "It will be preserved when you Apply tags with "
                     "'Keep existing covers'.")
            return
        try:
            im = Image.open(tmp)
            w, h = im.size
            im.thumbnail((96, 96))
            photo = ImageTk.PhotoImage(im)
            self._embedded_cover_img = photo
            self._emb_thumb.configure(image=photo, text="")
            self._emb_info.configure(
                text=f"Embedded front cover:  {w}×{h} px\n"
                     "Kept automatically when you Apply tags with\n"
                     "'Keep existing covers' (the default).")
        except Exception as e:
            self._emb_thumb.configure(text="✓ has\ncover", image="")
            self._emb_info.configure(text=f"Embedded cover detected (preview error: {e})")

    # ── Auto-number / Titles from filenames ───────────────────────────────────

    def _auto_number(self) -> None:
        n = len(self._audio_files)
        for i, f in enumerate(self._audio_files, 1):
            d = self._per_file.setdefault(f, {})
            d["TRACKNUMBER"] = str(i)
        if "TRACKTOTAL" in self._common_vars and not self._common_vars["TRACKTOTAL"].get():
            self._common_vars["TRACKTOTAL"].set(str(n))
        self._refresh_file_list()
        self._on_file_select()
        self.status.configure(text=f"Numbered {n} track(s)")

    def _titles_from_filenames(self) -> None:
        for f in self._audio_files:
            stem = Path(f).stem
            title, track = _auto_title_from_stem(stem)
            d = self._per_file.setdefault(f, {})
            d["TITLE"] = title
            if track and not d.get("TRACKNUMBER"):
                d["TRACKNUMBER"] = track
        self._refresh_file_list()
        self._on_file_select()
        self.status.configure(text=f"Filled titles for {len(self._audio_files)} file(s)")

    # ── Apply tags ────────────────────────────────────────────────────────────

    def _build_tags_for_file(self, file_path: str, file_index: int) -> dict[str, str]:
        """Build the full tag dict for one file."""
        common: dict[str, str] = {}
        for key, var in self._common_vars.items():
            val = var.get().strip()
            if val:
                common[key] = val
        # SOURCE from text widget
        src = self._get_source()
        if src:
            common["SOURCE"] = src
        # Notes
        notes = self.txt_notes.get("1.0", "end").strip()
        if notes:
            common["NOTES"] = notes

        # Derive ALBUM if not set
        if not common.get("ALBUM"):
            artist = common.get("ARTIST", "")
            date = common.get("DATE", "")
            if artist and date:
                common["ALBUM"] = f"{artist} - {date}"
            elif artist:
                common["ALBUM"] = artist
            elif date:
                common["ALBUM"] = date

        per = self._per_file.get(file_path, {})
        tags = dict(common)
        for k in PER_TRACK_TAGS:
            val = per.get(k, "")
            tags[k] = val  # empty = remove existing tag

        # If text file loaded and position is within tracks, fill from setlist
        if self._show:
            tracks = self._show.all_tracks()
            if file_index < len(tracks):
                t = tracks[file_index]
                if not tags.get("TITLE"):
                    tags["TITLE"] = t.title
                if not tags.get("TRACKNUMBER"):
                    tags["TRACKNUMBER"] = str(t.global_index)
                # DISCTOTAL = number of sets
                n_sets = len(self._show.sets)
                if not tags.get("DISCTOTAL"):
                    tags["DISCTOTAL"] = str(n_sets)
                if not tags.get("DISCNUMBER"):
                    tags["DISCNUMBER"] = str(
                        _set_number_for_track(self._show, t.global_index))
                if not tags.get("TRACKTOTAL"):
                    n_all = len(tracks)
                    tags["TRACKTOTAL"] = str(n_all)
                    common["TRACKTOTAL"] = str(n_all)

        return tags

    def _apply_tags(self) -> None:
        if not self._audio_files:
            messagebox.showwarning("Live Show Tagger", "Add some files first.")
            return
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror("Live Show Tagger",
                                 f"ffmpeg not found at:\n{ffmpeg}\n\n"
                                 "Install via Tools → Update all CLI tools.")
            return
        metaflac = get_tool("metaflac").path(self.config_obj)
        cover_action = self.var_cover_action.get()
        strip_first = self.var_strip_first.get()

        self.btn_apply.state(["disabled"])
        self.progress.configure(maximum=len(self._audio_files), value=0)

        threading.Thread(
            target=self._apply_worker,
            args=(list(self._audio_files), ffmpeg, metaflac, cover_action, strip_first),
            daemon=True,
        ).start()

    def _apply_worker(self, files: list[str], ffmpeg: Path, metaflac: Path,
                      cover_action: str, strip_first: bool) -> None:
        ok = 0
        errors: list[str] = []

        for i, f in enumerate(files):
            p = Path(f)
            self.after(0, lambda txt=f"[{i+1}/{len(files)}] {p.name}":
                       self.status.configure(text=txt))

            try:
                # --- Step 1: strip existing tags if requested ---
                if strip_first:
                    ext = p.suffix.lower()
                    if ext == ".flac" and metaflac.exists():
                        _strip_flac_tags(p, metaflac)
                    elif ffmpeg.exists():
                        _strip_ffmpeg_tags(p, ffmpeg)

                # --- Step 2: build tags ---
                tags = self._build_tags_for_file(f, i)

                # --- Step 3: determine cover handling ---
                front_cover = self._cover_paths.get(3)  # type 3 = Front Cover

                if cover_action == "remove":
                    # For FLAC with metaflac available: remove picture blocks
                    if p.suffix.lower() == ".flac" and metaflac.exists():
                        rc, err = _run_quiet([
                            str(metaflac), "--remove", "--block-type=PICTURE",
                            "--dont-use-padding", str(p),
                        ])
                        if rc != 0:
                            raise RuntimeError(f"metaflac picture remove failed: {err.strip()}")
                        tag_io.apply_tags_in_place(ffmpeg, p, tags,
                                                   cover=None, clear_cover=False)
                    else:
                        tag_io.apply_tags_in_place(ffmpeg, p, tags,
                                                   cover=None, clear_cover=True)

                elif cover_action == "replace":
                    # For FLAC: embed each selected cover type via metaflac; then write tags
                    if p.suffix.lower() == ".flac" and metaflac.exists():
                        # Remove existing pictures first
                        _run_quiet([
                            str(metaflac), "--remove", "--block-type=PICTURE",
                            "--dont-use-padding", str(p),
                        ])
                        # Import each selected cover
                        for pic_type, _, type_str in COVER_TYPES:
                            cp = self._cover_paths.get(pic_type)
                            if cp and cp.exists():
                                mime = "image/jpeg" if cp.suffix.lower() in (".jpg", ".jpeg") else "image/png"
                                spec = f"{type_str}|{mime}||0x0|{cp}"
                                rc, err = _run_quiet([
                                    str(metaflac), f"--import-picture-from={spec}", str(p),
                                ])
                                if rc != 0:
                                    raise RuntimeError(
                                        f"metaflac import-picture failed: {err.strip()}")
                        tag_io.apply_tags_in_place(ffmpeg, p, tags,
                                                   cover=None, clear_cover=False)
                    else:
                        # Non-FLAC: pass only front cover to ffmpeg
                        cv = front_cover if (front_cover and front_cover.exists()) else None
                        tag_io.apply_tags_in_place(ffmpeg, p, tags,
                                                   cover=cv, clear_cover=False)

                else:  # keep
                    # Prefer mutagen for "keep cover" mode — faster and supports
                    # M4A / Ogg / Opus which ffmpeg's -c copy cannot handle cleanly.
                    if mutagen_available() and p.suffix.lower() not in (".ape", ".shn", ".mkw"):
                        mutagen_write_tags(p, tags)
                    else:
                        tag_io.apply_tags_in_place(ffmpeg, p, tags,
                                                   cover=None, clear_cover=False)

                ok += 1

            except Exception as e:
                errors.append(f"{p.name}: {e}")

            self.after(0, lambda v=i+1: self.progress.configure(value=v))

        self.after(0, lambda: self._apply_done(ok, errors))

    def _apply_done(self, ok: int, errors: list[str]) -> None:
        self.btn_apply.state(["!disabled"])
        if errors:
            messagebox.showerror(
                "Live Show Tagger",
                f"Tagged {ok} file(s). {len(errors)} failed:\n\n"
                + "\n".join(errors[:10]),
            )
        else:
            messagebox.showinfo("Live Show Tagger",
                                f"Tagged {ok} file(s) successfully.")
        self.status.configure(text=f"Done. {ok} tagged, {len(errors)} failed.")

    # ── Generate text file ────────────────────────────────────────────────────

    def _generate_txt(self) -> None:
        fields = {
            "artist":   self._common_vars.get("ARTIST",   tk.StringVar()).get().strip(),
            "date":     self._common_vars.get("DATE",     tk.StringVar()).get().strip(),
            "venue":    self._common_vars.get("VENUE",    tk.StringVar()).get().strip(),
            "location": self._common_vars.get("LOCATION", tk.StringVar()).get().strip(),
            "source":   self._get_source(),
        }
        notes = self.txt_notes.get("1.0", "end").strip()
        sets = self._show.sets if self._show else []

        show = EtreeShow(
            artist=fields["artist"],
            date=fields["date"],
            venue=fields["venue"],
            location=fields["location"],
            source=fields["source"],
            notes=notes,
            sets=sets,
        )
        content = generate_etree_file(show)

        if self._audio_files:
            parent = Path(self._audio_files[0]).parent
            default_name = parent.name + ".txt"
            initial_dir = str(parent)
        elif self._txt_path:
            parent = self._txt_path.parent
            default_name = parent.name + ".txt"
            initial_dir = str(parent)
        else:
            default_name = "setlist.txt"
            initial_dir = self.config_obj.get("last_input_dir") or None

        out = filedialog.asksaveasfilename(
            parent=self, title="Save eTree text file",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not out:
            return
        try:
            Path(out).write_text(content, encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Live Show Tagger", f"Could not write file:\n{e}")
            return
        self.status.configure(text=f"Generated: {Path(out).name}")
