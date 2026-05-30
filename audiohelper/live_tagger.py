"""Live Show Tagger — eTree/furthur-style metadata tagger for live recordings.

Operates like foo_tradersfriend's Live Show Tagger component:
  1. Load a folder of audio files (auto-sorted lexicographically).
  2. Load a text info file (eTree format) — auto-detected in the folder.
  3. Files and tracks are matched positionally (file 1 → track 1, etc.).
  4. Edit Artist / Date / Venue / Location / Source with per-field Revert.
  5. Click "Tag Files" to write all tags in one batch.
  6. "Generate Text File" saves a canonical .txt next to the audio files.

Files ignored when scanning a folder:
  readme.txt, *ffp*, *fingerprint*, *md5*  (plus non-audio extensions)
"""

import re
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk, filedialog, messagebox
from typing import Optional

from . import tag_io
from .action_picker import AUDIO_EXTS
from .tools import get_tool


# ── Folder scan ignore patterns ───────────────────────────────────────────────

_IGNORE_PATTERNS = [
    re.compile(r'^readme\.txt$',  re.IGNORECASE),
    re.compile(r'ffp',            re.IGNORECASE),
    re.compile(r'fingerprint',    re.IGNORECASE),
    re.compile(r'md5',            re.IGNORECASE),
]


def _should_ignore(filename: str) -> bool:
    return any(p.search(filename) for p in _IGNORE_PATTERNS)


def read_text_smart(p: "Path") -> str:
    """Read a text file, trying UTF-8, then Windows-1252, then Latin-1.

    Real-world eTree/info files are frequently saved in Windows-1252 (cp1252)
    with smart quotes (’ “ ” – ). Decoding those as latin-1 turns them into
    control characters (shown as �). cp1252 decodes them correctly.
    """
    data = p.read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    # Last resort: never raise — replace undecodable bytes.
    return data.decode("utf-8", errors="replace")


# ── eTree data structures ─────────────────────────────────────────────────────

@dataclass
class EtreeTrack:
    global_index: int   # continuous across all sets (1-based); used for TRACKNUMBER
    set_index: int      # position within its set (1-based)
    title: str
    comment: str = ""


@dataclass
class EtreeSet:
    label: str          # "Set 1", "Set 2", "Encore", etc.
    tracks: list[EtreeTrack] = field(default_factory=list)


@dataclass
class EtreeShow:
    artist: str = ""
    date: str = ""
    venue: str = ""
    location: str = ""
    source: str = ""
    notes: str = ""
    sets: list[EtreeSet] = field(default_factory=list)

    def all_tracks(self) -> list[EtreeTrack]:
        return [t for s in self.sets for t in s.tracks]


# ── eTree parser ──────────────────────────────────────────────────────────────

_KEY_RE   = re.compile(r'^(Artist|Date|Venue|Location|Source)\s*:\s*(.*)$', re.IGNORECASE)
# Set / disc markers: "Set 1", "Disc 2", "CD 3", "Encore", "Encore 2", "E1"
_SET_RE   = re.compile(r'^(Set\s*\d+|Disc\s*\d+|CD\s*\d+|Encore\s*\d*|E\d+)[\s:]*$', re.IGNORECASE)
_TRACK_RE = re.compile(r'^(\d+)[.)]\s+(.*)$')

# Embedded date inside a free-form header line, e.g.
#   "The Rolling Stones 1999 06 08 Shepherds Bush Empire, London, UK"
#   "Phish 1997-12-31 Madison Square Garden"
_EMBEDDED_DATE_RE = re.compile(
    r'\b((?:19|20)\d{2})[\s._/-](0?[1-9]|1[0-2])[\s._/-](0?[1-9]|[12]\d|3[01])\b'
)


def parse_etree_file(content: str) -> EtreeShow:
    # Normalize line endings and strip UTF-8 BOM if present
    text = content.replace('\r\n', '\n').replace('\r', '\n')
    if text.startswith('﻿'):
        text = text[1:]
    lines = text.splitlines()

    show = EtreeShow()

    # Split at the first set-label or track line — everything before is the header block
    split_idx = len(lines)
    for i, raw in enumerate(lines):
        line = raw.strip()
        if _SET_RE.match(line) or _TRACK_RE.match(line):
            split_idx = i
            break
    header_raw  = lines[:split_idx]
    body_lines  = lines[split_idx:]

    # Choose header format: key-value ("Artist: ...") vs positional (bare lines)
    header_nonblank = [l.strip() for l in header_raw if l.strip()]
    if any(_KEY_RE.match(l) for l in header_nonblank):
        _parse_kv_header(show, header_nonblank)
    else:
        _parse_positional_header(show, header_raw)

    # Decide setlist mode: if any line is a numbered track ("1. Title"), use
    # strictly numbered parsing. Otherwise fall back to UNNUMBERED parsing,
    # where bare title lines under a Set/Disc header become tracks. This keeps
    # numbered files (which may have trailing junk like "*****" or a lineup)
    # unaffected while still handling unnumbered eTree setlists.
    numbered = any(_TRACK_RE.match(l.strip()) for l in body_lines)

    current_set: Optional[EtreeSet] = None
    global_idx = 0
    stop_capture = False   # unnumbered mode: set once we hit lineup/source/notes
    trailing: list[str] = []  # lines after the setlist (source/lineup/notes)

    def _new_set(line: str) -> EtreeSet:
        label = line.rstrip(':').strip()
        label = re.sub(r'^set\s*(\d+)$',  lambda x: f"Set {x.group(1)}",  label, flags=re.IGNORECASE)
        label = re.sub(r'^disc\s*(\d+)$', lambda x: f"Disc {x.group(1)}", label, flags=re.IGNORECASE)
        label = re.sub(r'^cd\s*(\d+)$',   lambda x: f"Disc {x.group(1)}", label, flags=re.IGNORECASE)
        label = re.sub(r'^encore\s*\d*$', 'Encore', label, flags=re.IGNORECASE)
        s = EtreeSet(label=label)
        show.sets.append(s)
        return s

    def _add_track(raw_title: str) -> None:
        nonlocal global_idx, current_set
        global_idx += 1
        comment = ""
        pm = re.search(r'\s*\(([^)]+)\)\s*$', raw_title)
        if pm:
            comment   = pm.group(1).strip()
            raw_title = raw_title[:pm.start()].strip()
        if current_set is None:
            current_set = EtreeSet(label="Set 1")
            show.sets.append(current_set)
        set_idx = len(current_set.tracks) + 1
        current_set.tracks.append(
            EtreeTrack(global_index=global_idx, set_index=set_idx,
                       title=raw_title, comment=comment))

    for raw in body_lines:
        line = raw.strip()
        if not line:
            continue

        if _SET_RE.match(line):
            current_set = _new_set(line)
            stop_capture = False   # a new set header resumes capture
            continue

        m = _TRACK_RE.match(line)
        if m:
            _add_track(m.group(2).strip())
            continue

        # Unnumbered mode: treat a bare line as a track unless it looks like
        # the end of the setlist (lineup, source/lineage, key:value, divider).
        if not numbered:
            if not stop_capture and _is_setlist_terminator(line):
                stop_capture = True
            if stop_capture:
                if not _DIVIDER_RE.match(line):
                    trailing.append(line)
                continue
            _add_track(line)

    # Fold any trailing post-setlist content (lineup, source, lineage, notes)
    # into source/notes if the header didn't already supply them.
    if trailing:
        src_lines  = [l for l in trailing if _line_is_source(l)]
        note_lines = [l for l in trailing if not _line_is_source(l)]
        if src_lines and not show.source:
            show.source = '\n'.join(src_lines).strip()
        if note_lines and not show.notes:
            show.notes = '\n'.join(note_lines).strip()

    return show


# Lines that signal the setlist has ended (lineup credits, source/lineage,
# key:value metadata, or a divider rule). Used only in unnumbered mode.
_LINEUP_RE = re.compile(
    r'\s[-–]\s.*\b(Guitar|Bass|Drums?|Vocals?|Keys?|Keyboards?|Piano|Organ|'
    r'Violin|Fiddle|Trumpet|Trombone|Sax|Saxophone|Percussion|Mandolin|Banjo|'
    r'Harmonica|Harp|Cello|Flute|Pedal\s+Steel|Lap\s+Steel|Synth)\b',
    re.IGNORECASE,
)
_KV_PREFIX_RE = re.compile(r'^[A-Za-z][\w /]{1,24}:\s', )
_DIVIDER_RE   = re.compile(r'^[\*\-=_~#]{3,}\s*$')


def _is_setlist_terminator(line: str) -> bool:
    if _DIVIDER_RE.match(line):
        return True
    if _KV_PREFIX_RE.match(line):
        return True
    if _LINEUP_RE.search(line):
        return True
    if _line_is_source(line):
        return True
    return False


def _parse_kv_header(show: EtreeShow, nonblank_lines: list[str]) -> None:
    """Parse header block where each field has an explicit 'Key: value' prefix."""
    note_lines: list[str] = []
    for line in nonblank_lines:
        m = _KEY_RE.match(line)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            if   key == 'artist':   show.artist   = val
            elif key == 'date':     show.date     = val
            elif key == 'venue':    show.venue    = val
            elif key == 'location': show.location = val
            elif key == 'source':   show.source   = val
        else:
            note_lines.append(line)
    show.notes = '\n'.join(note_lines).strip()


_SOURCE_PREFIX_RE = re.compile(
    r'^(Taper|Microphones?|Mic|Preamp|Recorders?|Recording\s+by|Taped\s+by'
    r'|Transfer(?:red)?\s+by|Transfer|Mastered\s+by|Edited\s+by|Lineage|Source)'
    r'\s*[:\-]',
    re.IGNORECASE,
)

_SOURCE_EQUIPMENT_WORDS = re.compile(
    r'\b(Schoeps|Neumann|DPA|AKG|Sennheiser|Oktava|Audio[.\-]Technica|Rode'
    r'|Nakamichi|Tascam|Zoom\s+H|Sony\s+PCM|SBD|DAT\b|FLAC\b|WAV\b)',
    re.IGNORECASE,
)


def _line_is_source(line: str) -> bool:
    """Return True if this line looks like a source/lineage line."""
    if ' > ' in line:
        return True
    if _SOURCE_PREFIX_RE.match(line):
        return True
    if _SOURCE_EQUIPMENT_WORDS.search(line):
        return True
    return False


def _parse_positional_header(show: EtreeShow, raw_lines: list[str]) -> None:
    """Parse header block in positional format:
       line 1 → Artist
       line 2 → Date
       line 3 → Venue (and Location if " - City, State" suffix is present)
       blank line separates header from source / notes below

    Remaining lines are classified by content:
      - Lines containing signal-chain notation, equipment keywords, or taper
        prefixes go to show.source (joined with newlines).
      - All other non-blank lines go to show.notes.
    """
    nonblank = [l.strip() for l in raw_lines if l.strip()]
    if not nonblank:
        return

    # First try: a single line that jams artist + date + venue together, e.g.
    #   "The Rolling Stones 1999 06 08 Shepherds Bush Empire, London, UK"
    # If line 1 has an embedded date, split around it and consume only line 1
    # as the header — everything else is notes/source.
    header_consumed = 1
    dm = _EMBEDDED_DATE_RE.search(nonblank[0])
    if dm:
        y, mo, dy = dm.group(1), dm.group(2), dm.group(3)
        show.date = f"{y}-{int(mo):02d}-{int(dy):02d}"
        before = nonblank[0][:dm.start()].strip(" -,")
        after  = nonblank[0][dm.end():].strip(" -,")
        if before:
            show.artist = before
        if after:
            _split_venue_location(show, after)
    else:
        # Classic positional format: line1=Artist, line2=Date, line3=Venue,
        # and optionally line4=Location ("City, ST" / "City, Country").
        show.artist = nonblank[0]
        if len(nonblank) >= 2:
            show.date = nonblank[1]
        if len(nonblank) >= 3:
            _split_venue_location(show, nonblank[2])
        header_consumed = 3
        # If venue didn't itself carry a location and the next line looks like
        # "City, State" — a comma-separated short line that isn't a source/
        # lineage line — treat it as the location.
        if (not show.location and len(nonblank) >= 4
                and _looks_like_location(nonblank[3])):
            show.location = nonblank[3]
            header_consumed = 4

    # Classify remaining lines (after the consumed header) into source vs notes.
    skipped = 0
    past_header = False
    source_lines: list[str] = []
    note_lines:   list[str] = []

    for raw in raw_lines:
        line = raw.strip()
        if not past_header:
            if line:
                skipped += 1
                if skipped == header_consumed:
                    past_header = True
            continue
        if not line:
            continue
        if _line_is_source(line):
            source_lines.append(line)
        else:
            note_lines.append(line)

    show.source = '\n'.join(source_lines).strip()
    show.notes  = '\n'.join(note_lines).strip()


def _looks_like_location(line: str) -> bool:
    """True if *line* looks like a 'City, ST' / 'City, Country' location."""
    if ',' not in line:
        return False
    if _line_is_source(line):
        return False
    # Short, comma-separated, no signal-chain or key:value markers.
    if ' > ' in line or ':' in line:
        return False
    return len(line.split()) <= 6


def _split_venue_location(show: EtreeShow, text: str) -> None:
    """Split 'Venue - City, ST' or 'Venue, City, Country' into venue + location."""
    # "Venue Name - City, ST" → split on last " - "
    parts = text.rsplit(' - ', 1)
    if len(parts) == 2 and re.search(r'.+,\s*\S', parts[1]):
        show.venue    = parts[0].strip()
        show.location = parts[1].strip()
        return
    # "Venue, City, State/Country" → first chunk is venue, rest is location
    chunks = [c.strip() for c in text.split(',')]
    if len(chunks) >= 3:
        show.venue    = chunks[0]
        show.location = ', '.join(chunks[1:])
    elif len(chunks) == 2:
        show.venue    = chunks[0]
        show.location = chunks[1]
    else:
        show.venue = text.strip()


def generate_etree_file(show: EtreeShow) -> str:
    lines: list[str] = []
    if show.artist:   lines.append(f"Artist: {show.artist}")
    if show.date:     lines.append(f"Date: {show.date}")
    if show.venue:    lines.append(f"Venue: {show.venue}")
    if show.location: lines.append(f"Location: {show.location}")
    if show.source:   lines.append(f"Source: {show.source}")

    if show.notes:
        lines.append("")
        lines.append(show.notes)

    for s in show.sets:
        lines.append("")
        lines.append(f"{s.label}:")
        for t in s.tracks:
            suffix = f" ({t.comment})" if t.comment else ""
            lines.append(f"{t.global_index}. {t.title}{suffix}")

    return '\n'.join(lines).strip() + '\n'


# ── Dialog ────────────────────────────────────────────────────────────────────

class LiveTaggerDialog(tk.Toplevel):
    def __init__(self, parent, config, runner, initial_files=None):
        super().__init__(parent)
        self.title("Live Show Tagger")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("1060x660")
        self.minsize(820, 520)

        self._audio_files: list[str] = []
        self._show: Optional[EtreeShow] = None
        self._orig_show: Optional[EtreeShow] = None
        self._txt_path: Optional[Path] = None
        self._source_widget: Optional[tk.Text] = None

        self._build_toolbar()
        self._build_body()
        self.status = ttk.Label(self, text="Load a folder to begin.",
                                anchor="w", relief="sunken", padding=(6, 2))
        self.status.pack(fill="x", side="bottom")

        if initial_files:
            self._load_from_initial(initial_files)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Load folder…",    command=self._load_folder).pack(side="left")
        ttk.Button(bar, text="Load text file…", command=self._load_txt_dialog).pack(side="left", padx=4)
        ttk.Button(bar, text="Reload text",     command=self._reload_txt).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Clear all",       command=self._clear_all).pack(side="left")

    def _build_body(self) -> None:
        outer = ttk.Frame(self, padding=(8, 0, 8, 4))
        outer.pack(fill="both", expand=True)

        # ── Resizable horizontal split ────────────────────────────────────
        paned = ttk.PanedWindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # ── Left pane: matched file list ──────────────────────────────────
        left = ttk.LabelFrame(paned, text="Audio files", padding=4)

        cols = ("num", "filename", "title", "st")
        self.tv = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse")
        self.tv.heading("num",      text="#",       anchor="center")
        self.tv.heading("filename", text="Filename", anchor="w")
        self.tv.heading("title",    text="Title",    anchor="w")
        self.tv.heading("st",       text="",         anchor="center")
        self.tv.column("num",      width=30,  minwidth=28,  stretch=False, anchor="center")
        self.tv.column("filename", width=180, minwidth=80,  stretch=True,  anchor="w")
        self.tv.column("title",    width=160, minwidth=60,  stretch=True,  anchor="w")
        self.tv.column("st",       width=22,  minwidth=22,  stretch=False, anchor="center")
        tv_sb = ttk.Scrollbar(left, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=tv_sb.set)
        tv_sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.tag_configure("matched", foreground="#007700")
        self.tv.tag_configure("extra",   foreground="#cc6600")

        paned.add(left, weight=2)

        # ── Right pane: show data + action buttons + setlist ──────────────
        right = ttk.Frame(paned, padding=(8, 0, 0, 0))

        hdr = ttk.LabelFrame(right, text="Show data", padding=8)
        hdr.pack(fill="x")
        hdr.columnconfigure(1, weight=1)

        self._field_vars: dict[str, tk.StringVar] = {}
        for row_i, (label, key) in enumerate([
            ("Artist",   "ARTIST"),
            ("Date",     "DATE"),
            ("Venue",    "VENUE"),
            ("Location", "LOCATION"),
        ]):
            ttk.Label(hdr, text=f"{label}:").grid(row=row_i, column=0, sticky="w", pady=2)
            var = tk.StringVar()
            self._field_vars[key] = var
            ttk.Entry(hdr, textvariable=var).grid(
                row=row_i, column=1, sticky="ew", padx=6, pady=2)
            ttk.Button(hdr, text="Revert", width=6,
                       command=lambda k=key: self._revert_field(k)
                       ).grid(row=row_i, column=2, sticky="w", pady=2)

        # Source — multi-line text widget
        row_src = len(self._field_vars)
        ttk.Label(hdr, text="Source:").grid(row=row_src, column=0, sticky="nw", pady=2)
        self._source_widget = tk.Text(hdr, height=3, font=("Segoe UI", 9), wrap="word")
        self._source_widget.grid(row=row_src, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(hdr, text="Revert", width=6,
                   command=lambda: self._revert_field("SOURCE")
                   ).grid(row=row_src, column=2, sticky="nw", pady=2)

        # Action buttons — packed BEFORE setlist so they're always visible
        act = ttk.Frame(right)
        act.pack(fill="x", pady=(8, 0), side="bottom")
        ttk.Button(act, text="Generate text file", command=self._generate_txt).pack(side="left")
        ttk.Button(act, text="Close",              command=self.destroy).pack(side="right")
        self.btn_tag = ttk.Button(act, text="Tag Files", command=self._tag_files,
                                  state="disabled")
        self.btn_tag.pack(side="right", padx=4)

        # Setlist read-only view — fills remaining space between Show data and buttons
        sl = ttk.LabelFrame(right, text="Setlist", padding=4)
        sl.pack(fill="both", expand=True, pady=(8, 4))
        self.txt_setlist = tk.Text(sl, font=("Consolas", 9), wrap="none",
                                   state="disabled", background="#f8f8f8")
        sl_sby = ttk.Scrollbar(sl, orient="vertical",   command=self.txt_setlist.yview)
        sl_sbx = ttk.Scrollbar(sl, orient="horizontal", command=self.txt_setlist.xview)
        self.txt_setlist.configure(yscrollcommand=sl_sby.set, xscrollcommand=sl_sbx.set)
        sl_sby.pack(side="right", fill="y")
        sl_sbx.pack(side="bottom", fill="x")
        self.txt_setlist.pack(fill="both", expand=True)
        self.txt_setlist.tag_configure("set_label", font=("Consolas", 9, "bold"))

        paned.add(right, weight=3)

    # ── Loading helpers ───────────────────────────────────────────────────────

    def _load_folder(self, folder: Optional[str] = None) -> None:
        if folder is None:
            folder = filedialog.askdirectory(
                parent=self, title="Select show folder",
                initialdir=self.config_obj.get("last_input_dir") or None,
            )
        if not folder:
            return
        d = Path(folder)
        files = sorted(
            str(p) for p in d.iterdir()
            if p.is_file()
            and p.suffix.lower() in AUDIO_EXTS
            and not _should_ignore(p.name)
        )
        if not files:
            messagebox.showwarning("Live Show Tagger",
                                   f"No supported audio files found in:\n{d}")
            return
        self._audio_files = files
        self.config_obj["last_input_dir"] = str(d)
        self.config_obj.save()

        # Auto-detect a .txt file in the same folder if none loaded yet
        if self._txt_path is None or self._txt_path.parent.resolve() != d.resolve():
            txts = sorted(
                p for p in d.iterdir()
                if p.suffix.lower() == ".txt" and not _should_ignore(p.name)
            )
            if txts:
                self._load_txt_path(txts[0])

        self._refresh_file_list()

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
        self._populate_fields()
        self._refresh_setlist_view()
        self._refresh_file_list()
        self.status.configure(text=f"Loaded: {p.name}")

    def _reload_txt(self) -> None:
        if self._txt_path and self._txt_path.exists():
            self._load_txt_path(self._txt_path)
        else:
            self.status.configure(text="No text file loaded.")

    def _clear_all(self) -> None:
        self._audio_files = []
        self._show = None
        self._orig_show = None
        self._txt_path = None
        for v in self._field_vars.values():
            v.set("")
        if self._source_widget:
            self._source_widget.delete("1.0", "end")
        self._refresh_file_list()
        self._refresh_setlist_view()
        self.status.configure(text="Cleared.")

    def _load_from_initial(self, initial_files: list[str]) -> None:
        audio = sorted(
            f for f in initial_files
            if Path(f).suffix.lower() in AUDIO_EXTS
            and not _should_ignore(Path(f).name)
        )
        if not audio:
            return
        self._audio_files = audio
        # Auto-detect txt in the common parent
        parent = Path(audio[0]).parent
        if all(Path(f).parent == parent for f in audio):
            txts = sorted(
                p for p in parent.iterdir()
                if p.suffix.lower() == ".txt" and not _should_ignore(p.name)
            )
            if txts:
                self._load_txt_path(txts[0])
        self._refresh_file_list()

    # ── Field population & revert ─────────────────────────────────────────────

    def _populate_fields(self) -> None:
        if not self._show:
            return
        self._field_vars["ARTIST"].set(self._show.artist)
        self._field_vars["DATE"].set(self._show.date)
        self._field_vars["VENUE"].set(self._show.venue)
        self._field_vars["LOCATION"].set(self._show.location)
        if self._source_widget:
            self._source_widget.delete("1.0", "end")
            self._source_widget.insert("1.0", self._show.source)

    def _revert_field(self, key: str) -> None:
        if not self._orig_show:
            return
        val = getattr(self._orig_show, key.lower(), "")
        if key == "SOURCE":
            if self._source_widget:
                self._source_widget.delete("1.0", "end")
                self._source_widget.insert("1.0", val)
        else:
            self._field_vars[key].set(val)

    def _get_source(self) -> str:
        if self._source_widget:
            return self._source_widget.get("1.0", "end").strip()
        return ""

    def _current_fields(self) -> dict[str, str]:
        return {
            "artist":   self._field_vars["ARTIST"].get().strip(),
            "date":     self._field_vars["DATE"].get().strip(),
            "venue":    self._field_vars["VENUE"].get().strip(),
            "location": self._field_vars["LOCATION"].get().strip(),
            "source":   self._get_source(),
        }

    # ── Refresh views ─────────────────────────────────────────────────────────

    def _refresh_file_list(self) -> None:
        for row in self.tv.get_children():
            self.tv.delete(row)

        tracks      = self._show.all_tracks() if self._show else []
        n_files     = len(self._audio_files)
        n_tracks    = len(tracks)
        matched     = min(n_files, n_tracks)

        for i, f in enumerate(self._audio_files):
            if i < n_tracks:
                t     = tracks[i]
                title = t.title
                st    = "✓"
                tag   = "matched"
            else:
                title = ""
                st    = "?"
                tag   = "extra"
            self.tv.insert("", "end",
                           values=(i + 1, Path(f).name, title, st),
                           tags=(tag,))

        # Status summary
        parts: list[str] = []
        if n_files:
            parts.append(f"{n_files} file(s)")
        if n_tracks:
            parts.append(f"{n_tracks} track(s) in setlist")
        if n_files and n_tracks:
            parts.append(f"{matched} matched")
            extra_files  = max(0, n_files  - n_tracks)
            extra_tracks = max(0, n_tracks - n_files)
            if extra_files:
                parts.append(f"{extra_files} file(s) without a track")
            if extra_tracks:
                parts.append(f"{extra_tracks} track(s) without a file")
        self.status.configure(
            text="   •   ".join(parts) if parts else "Load a folder to begin.")

        can_tag = bool(self._audio_files and self._show and matched > 0)
        self.btn_tag.configure(state="normal" if can_tag else "disabled")

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

    # ── Tag files ─────────────────────────────────────────────────────────────

    def _tag_files(self) -> None:
        if not self._audio_files or not self._show:
            return
        ffmpeg = get_tool("ffmpeg").path(self.config_obj)
        if not ffmpeg.exists():
            messagebox.showerror("Live Show Tagger",
                                 f"ffmpeg not found at:\n{ffmpeg}\n\n"
                                 "Install via Tools → Update all CLI tools.")
            return

        fields  = self._current_fields()
        tracks  = self._show.all_tracks()
        n_total = len(tracks)
        n_sets  = len(self._show.sets)
        album   = (f"{fields['artist']} - {fields['date']}"
                   if fields["artist"] and fields["date"]
                   else fields["artist"] or fields["date"])
        pairs   = list(zip(self._audio_files, tracks))

        self.btn_tag.configure(state="disabled")
        self.status.configure(text=f"Tagging {len(pairs)} file(s)…")

        def _worker() -> None:
            ok = 0
            errors: list[str] = []
            for f, track in pairs:
                set_num = _set_number(self._show, track.global_index)
                tags: dict[str, str] = {
                    "ARTIST":      fields["artist"],
                    "ALBUMARTIST": fields["artist"],
                    "ALBUM":       album,
                    "DATE":        fields["date"],
                    "VENUE":       fields["venue"],
                    "LOCATION":    fields["location"],
                    "SOURCE":      fields["source"],
                    "TITLE":       track.title,
                    "TRACKNUMBER": str(track.global_index),
                    "TRACKTOTAL":  str(n_total),
                    "DISCNUMBER":  str(set_num),
                    "DISCTOTAL":   str(n_sets),
                    "COMMENT":     track.comment,
                }
                # Don't write keys with empty values
                tags = {k: v for k, v in tags.items() if v}
                try:
                    tag_io.apply_tags_in_place(ffmpeg, Path(f), tags)
                    ok += 1
                except Exception as e:
                    errors.append(f"{Path(f).name}: {e}")
            self.after(0, lambda: _done(ok, errors))

        def _done(ok: int, errors: list[str]) -> None:
            self.btn_tag.configure(state="normal")
            if errors:
                messagebox.showerror(
                    "Live Show Tagger",
                    f"Tagged {ok} file(s). {len(errors)} failed:\n\n"
                    + "\n".join(errors[:10]),
                )
            else:
                messagebox.showinfo("Live Show Tagger",
                                    f"Tagged {ok} file(s) successfully.")
            self.status.configure(
                text=f"Done. {ok} tagged, {len(errors)} failed.")

        threading.Thread(target=_worker, daemon=True).start()

    # ── Generate text file ────────────────────────────────────────────────────

    def _generate_txt(self) -> None:
        if not self._show and not self._audio_files:
            messagebox.showwarning("Live Show Tagger",
                                   "Load a folder and text file first.")
            return

        fields = self._current_fields()
        show   = EtreeShow(
            artist=fields["artist"],
            date=fields["date"],
            venue=fields["venue"],
            location=fields["location"],
            source=fields["source"],
            notes=self._show.notes if self._show else "",
            sets=self._show.sets  if self._show else [],
        )
        content = generate_etree_file(show)

        if self._audio_files:
            parent       = Path(self._audio_files[0]).parent
            default_name = parent.name + ".txt"
            initial_dir  = str(parent)
        elif self._txt_path:
            parent       = self._txt_path.parent
            default_name = parent.name + ".txt"
            initial_dir  = str(parent)
        else:
            default_name = "setlist.txt"
            initial_dir  = self.config_obj.get("last_input_dir") or None

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _set_number(show: EtreeShow, global_index: int) -> int:
    for i, s in enumerate(show.sets, 1):
        for t in s.tracks:
            if t.global_index == global_index:
                return i
    return 1
