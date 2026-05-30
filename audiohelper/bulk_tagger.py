"""Bulk Tag Cleanup — scan a library folder and normalize concert tags.

Ported and adapted from TagCleaner (github.com/auxren/TagCleaner). Recursively
finds show folders, infers Artist / Date / Venue / City / Source from folder
names, info.txt files, and existing tags, builds a canonical album name, scores
confidence, and writes normalized tags after the user reviews and approves.

Canonical album: "YYYY-MM-DD Venue, City, Region [SBD/mics]"
Tags written: ARTIST, ALBUMARTIST, ALBUM, DATE, TITLE, TRACKNUMBER,
              TRACKTOTAL, DISCNUMBER (when multi-disc).
"""

import re
import threading
import tkinter as tk
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from . import theme as _t
from .action_picker import AUDIO_EXTS
from .live_tagger import parse_etree_file, read_text_smart, _should_ignore
from .tc_sources import detect_source
from .tc_tagger import mutagen_available, read_tags_mutagen, write_tags
from .tools import get_tool


# ── Date / artist inference (ported from TagCleaner parser) ───────────────────

ISO_DATE     = re.compile(r"(?<!\d)((?:19|20)\d{2})[-._/](0?[1-9]|1[0-2])[-._/](0?[1-9]|[12]\d|3[01])(?!\d)")
COMPACT_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
SPLIT_COMPACT= re.compile(r"(?<!\d)((?:19|20)\d{2})[._](0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")
US_FULL_DATE = re.compile(r"(?<!\d)(0?[1-9]|1[0-2])[-._/](0?[1-9]|[12]\d|3[01])[-._/]((?:19|20)\d{2})(?!\d)")
US_SHORT_DATE= re.compile(r"(?<!\d)(0?[1-9]|1[0-2])[-._/](0?[1-9]|[12]\d|3[01])[-._/](\d{2})(?!\d)")

US_STATE_CODE = re.compile(
    r"(?:A[LKZR]|C[AOT]|DE|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|"
    r"N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])")
_COUNTRIES = ("England", "Scotland", "Wales", "Ireland", "Canada", "Germany",
              "France", "Netherlands", "Italy", "Spain", "Japan", "Australia",
              "UK", "USA", "Belgium", "Sweden", "Norway", "Denmark")

# eTree band-abbreviation prefixes (subset of the community list)
ARTIST_PREFIX_MAP = {
    "gd": "Grateful Dead", "ph": "Phish", "phish": "Phish", "wsp": "Widespread Panic",
    "dmb": "Dave Matthews Band", "abb": "Allman Brothers Band", "moe": "moe.",
    "sci": "String Cheese Incident", "um": "Umphrey's McGee", "dso": "Dark Star Orchestra",
    "jgb": "Jerry Garcia Band", "phil": "Phil Lesh & Friends", "ratdog": "RatDog",
    "trey": "Trey Anastasio", "mule": "Gov't Mule", "bt": "Blues Traveler",
    "db": "Disco Biscuits", "ymsb": "Yonder Mountain String Band", "ween": "Ween",
    "wilco": "Wilco", "pj": "Pearl Jam", "rre": "Railroad Earth", "ls": "Leftover Salmon",
    "kw": "Keller Williams", "mmw": "Medeski Martin & Wood", "dead": "The Dead",
    "glaf": "Grahame Lesh & Friends",
}


def parse_date_from(text: str) -> Optional[str]:
    """Return ISO YYYY-MM-DD from *text*, or None."""
    m = ISO_DATE.search(text)
    if m:
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            pass
    for pat in (COMPACT_DATE, SPLIT_COMPACT):
        m = pat.search(text)
        if m:
            try:
                return _date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
            except ValueError:
                pass
    m = US_FULL_DATE.search(text)
    if m:
        try:
            return _date(int(m.group(3)), int(m.group(1)), int(m.group(2))).strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = US_SHORT_DATE.search(text)
    if m:
        try:
            yy = int(m.group(3))
            year = 1900 + yy if yy >= 60 else 2000 + yy
            return _date(year, int(m.group(1)), int(m.group(2))).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _date_position(text: str) -> Optional[int]:
    best = None
    for pat in (ISO_DATE, COMPACT_DATE, SPLIT_COMPACT, US_FULL_DATE, US_SHORT_DATE):
        m = pat.search(text)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def _clean_artist(text: str) -> Optional[str]:
    t = text.strip(" -,_()[]\t")
    t = re.sub(r"^\d{1,3}\s+(?=[A-Za-z])", "", t)
    t = re.split(r"\s+-\s+|\s+\(", t, maxsplit=1)[0]
    t = t.strip(" -,_()[]\t")
    if not t or not any(c.isalpha() for c in t) or len(t) < 2 or len(t) > 60:
        return None
    return t


def artist_from_folder(name: str) -> Optional[str]:
    # eTree prefix: "gd67-08-05" → Grateful Dead
    m = re.match(r"^([a-z]+)\d", name.lower())
    if m and m.group(1) in ARTIST_PREFIX_MAP:
        return ARTIST_PREFIX_MAP[m.group(1)]
    # "YYYY-MM-DD - Artist - Venue"
    m = re.match(r"^(?:19|20)\d{2}[-.]\d{2}[-.]\d{2}\s*-\s*([^-]+?)\s*-", name)
    if m:
        return m.group(1).strip()
    # "Artist YYYY-MM-DD …" — everything before the date
    pos = _date_position(name)
    if pos and pos > 0:
        prefix = name[:pos].strip(" -,_()[]\t")
        if len(prefix) > 2 and not (len(prefix) == 3 and prefix.islower()):
            return _clean_artist(prefix)
    return None


def city_region_from_folder(name: str) -> tuple[Optional[str], Optional[str]]:
    m = re.search(r",\s*([A-Z][A-Za-z.'\- ]+?),\s*([A-Z]{2})\b", name)
    if m and US_STATE_CODE.fullmatch(m.group(2)):
        return m.group(1).strip(), m.group(2)
    m = re.search(r",\s*([A-Z][A-Za-z.'\- ]+?),\s*(" + "|".join(_COUNTRIES) + r")\b", name)
    if m:
        return m.group(1).strip(), m.group(2)
    return None, None


# ── Concert model ─────────────────────────────────────────────────────────────

@dataclass
class ConcertFolder:
    folder: Path
    audio_files: list[Path] = field(default_factory=list)
    artist: str = ""
    date: str = ""
    venue: str = ""
    city: str = ""
    region: str = ""
    source: str = ""
    n_tracks_setlist: int = 0
    titles: list[str] = field(default_factory=list)   # per-file title (setlist or existing)
    discs: list[int] = field(default_factory=list)    # per-file disc number
    approved: bool = True
    note: str = ""

    def album_name(self) -> str:
        parts: list[str] = []
        if self.date:
            parts.append(self.date)
        place = ", ".join(p for p in (self.venue, self.city, self.region) if p)
        if place:
            parts.append(place)
        album = " ".join(parts).strip()
        src = detect_source(self.source).label() if self.source else ""
        if src:
            album = f"{album} {src}".strip()
        return album or self.artist

    def confidence(self) -> float:
        s = 0.0
        if self.artist: s += 0.25
        if self.date:   s += 0.25
        if self.venue:  s += 0.15
        if self.city:   s += 0.10
        if self.titles and self.audio_files:
            s += 0.25 if len([t for t in self.titles if t]) == len(self.audio_files) else 0.05
        return round(s, 2)


# ── Scanning & inference ──────────────────────────────────────────────────────

_DISC_FOLDER_RE = re.compile(r"^(?:cd|disc|disk|d)\s*[-_]?\s*(\d{1,2})$", re.IGNORECASE)


def _audio_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO_EXTS
                  and not _should_ignore(p.name))


def scan_library(root: Path) -> list["ConcertFolder"]:
    """Return ConcertFolder stubs (folder + ordered audio files).

    Multi-disc shows whose audio lives in CD1/CD2/Disc 1 subfolders are
    merged into ONE concert rooted at the parent, with files concatenated
    in disc order. A wrapped folder (folder/folder/*.flac) collapses too.
    """
    # Folders directly holding audio.
    audio_folders: set[Path] = set()
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not _should_ignore(p.name):
            audio_folders.add(p.parent)

    concerts: list[ConcertFolder] = []
    consumed: set[Path] = set()

    for folder in sorted(audio_folders):
        if folder in consumed:
            continue
        # Is this a disc subfolder (CD1, Disc 2…) with disc siblings?
        if _DISC_FOLDER_RE.match(folder.name):
            parent = folder.parent
            disc_subs = sorted(
                (d for d in parent.iterdir()
                 if d.is_dir() and _DISC_FOLDER_RE.match(d.name) and _audio_in(d)),
                key=lambda d: int(_DISC_FOLDER_RE.match(d.name).group(1)))
            if len(disc_subs) >= 1:
                files: list[Path] = []
                discs: list[int] = []
                for d in disc_subs:
                    dn = int(_DISC_FOLDER_RE.match(d.name).group(1))
                    for f in _audio_in(d):
                        files.append(f)
                        discs.append(dn)
                    consumed.add(d)
                concerts.append(ConcertFolder(folder=parent, audio_files=files, discs=discs))
                continue
        concerts.append(ConcertFolder(folder=folder, audio_files=_audio_in(folder)))
    return concerts


def _find_info_txt(folder: Path) -> Optional[Path]:
    """Look for a setlist/info .txt in *folder*, then walk up to 2 parents."""
    for f in [folder, *list(folder.parents)[:2]]:
        txts = sorted(p for p in f.glob("*.txt") if not _should_ignore(p.name))
        if txts:
            return txts[0]
    return None


def infer_concert(stub: "ConcertFolder", ffprobe: Optional[Path]) -> ConcertFolder:
    folder = stub.folder
    audio = stub.audio_files
    c = ConcertFolder(folder=folder, audio_files=audio, discs=list(stub.discs))
    if not audio:
        return c

    # 1) Existing tags from the first file (mutagen → fast)
    existing = read_tags_mutagen(audio[0]) if mutagen_available() else {}

    # 2) info.txt / setlist .txt — search this folder then up to 2 parents
    #    (multi-disc shows keep the setlist in the show root, not in CD1/CD2).
    show = None
    txt = _find_info_txt(folder)
    if txt:
        try:
            show = parse_etree_file(read_text_smart(txt))
        except Exception:
            show = None

    # 3) Folder-name inference (look at this folder and its parents)
    names = [folder.name] + [p.name for p in folder.parents[:2]]
    name_blob = "  ".join(names)

    # Resolve fields with precedence: setlist text > existing tags > folder name
    c.artist = (show.artist if show and show.artist else "") \
        or existing.get("ARTIST", "") or existing.get("ALBUMARTIST", "") \
        or (artist_from_folder(folder.name) or artist_from_folder(name_blob) or "")
    c.date = (show.date if show and show.date else "") \
        or existing.get("DATE", "") \
        or (parse_date_from(folder.name) or parse_date_from(name_blob) or "")
    c.venue = (show.venue if show and show.venue else "") or existing.get("VENUE", "")
    if show and show.location:
        # location is "City, Region"
        loc_parts = [x.strip() for x in show.location.split(",")]
        c.city = loc_parts[0] if loc_parts else ""
        c.region = loc_parts[-1] if len(loc_parts) > 1 else ""
    if not c.city:
        city, region = city_region_from_folder(name_blob)
        c.city = city or ""
        c.region = region or c.region
    c.source = (show.source if show and show.source else "") or existing.get("SOURCE", "")

    # 4) Per-file titles + disc numbers
    tracks = show.all_tracks() if show else []
    c.n_tracks_setlist = len(tracks)
    # discs were pre-assigned from CD1/CD2 subfolders → keep them; else compute.
    have_disc_folders = bool(stub.discs)
    new_discs: list[int] = []
    for i, f in enumerate(audio):
        title = ""
        if i < len(tracks):
            title = tracks[i].title
            disc = (stub.discs[i] if have_disc_folders
                    else (_disc_of(show, tracks[i].global_index) if show else 1))
        else:
            ex = read_tags_mutagen(f) if mutagen_available() else {}
            title = ex.get("TITLE", "") or _title_from_filename(f.stem)
            if have_disc_folders:
                disc = stub.discs[i]
            else:
                d = ex.get("DISCNUMBER", "")
                m = re.match(r"\s*(\d+)", str(d))
                disc = int(m.group(1)) if m else 1
        c.titles.append(title)
        new_discs.append(disc)
    c.discs = new_discs

    return c


def _disc_of(show, global_index: int) -> int:
    for i, s in enumerate(show.sets, 1):
        for t in s.tracks:
            if t.global_index == global_index:
                # "Disc N" labels map to N; Set/Encore map to sequential index
                m = re.match(r"disc\s*(\d+)", s.label, re.IGNORECASE)
                return int(m.group(1)) if m else i
    return 1


def _title_from_filename(stem: str) -> str:
    m = re.match(r"^\s*(?:d\d+t\d+|\d{1,3})\s*[-._\s]+\s*(.+)$", stem)
    title = m.group(1) if m else stem
    title = re.sub(r"[_\.]+", " ", title)
    return re.sub(r"\s+", " ", title).strip()


# ── Apply tags ────────────────────────────────────────────────────────────────

def apply_concert(c: ConcertFolder) -> tuple[int, list[str]]:
    """Write normalized tags to all files in a concert. Returns (ok, errors)."""
    if not mutagen_available():
        return 0, ["mutagen not installed"]
    album = c.album_name()
    n = len(c.audio_files)
    disc_total = max(c.discs) if c.discs else 1
    # per-disc track totals
    per_disc: dict[int, int] = {}
    for d in c.discs:
        per_disc[d] = per_disc.get(d, 0) + 1
    # per-disc running track number
    disc_counter: dict[int, int] = {}
    ok, errors = 0, []
    for i, f in enumerate(c.audio_files):
        disc = c.discs[i] if i < len(c.discs) else 1
        disc_counter[disc] = disc_counter.get(disc, 0) + 1
        track_no = disc_counter[disc]
        tags = {
            "ARTIST": c.artist, "ALBUMARTIST": c.artist, "ALBUM": album,
            "DATE": c.date, "VENUE": c.venue,
            "LOCATION": ", ".join(p for p in (c.city, c.region) if p),
            "SOURCE": c.source,
            "TITLE": c.titles[i] if i < len(c.titles) else "",
            "TRACKNUMBER": f"{track_no:02d}",
            "TRACKTOTAL": str(per_disc.get(disc, n)),
        }
        if disc_total > 1:
            tags["DISCNUMBER"] = str(disc)
            tags["DISCTOTAL"] = str(disc_total)
        tags = {k: v for k, v in tags.items() if v}
        try:
            write_tags(f, tags)
            ok += 1
        except Exception as e:
            errors.append(f"{f.name}: {e}")
    return ok, errors


# ── Dialog ────────────────────────────────────────────────────────────────────

class BulkTaggerDialog(tk.Toplevel):
    def __init__(self, parent, config, runner):
        super().__init__(parent)
        self.title("Bulk Tag Cleanup")
        self.config_obj = config
        self.runner = runner
        self.transient(parent)
        self.geometry("1120x720")
        self.minsize(900, 560)
        _t.apply(self)

        self._concerts: list[ConcertFolder] = []
        self._scanning = False

        self._build_toolbar()
        self._build_body()
        self._build_bottom()
        self.status = ttk.Label(self, text="Choose a library folder to scan.",
                                anchor="w", style="Status.TLabel")
        self.status.pack(fill="x", side="bottom")

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 8, 8, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Scan folder…", style="Action.TButton",
                   command=self._scan_dialog).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Approve all", command=lambda: self._set_all(True),
                   style="Ghost.TButton").pack(side="left")
        ttk.Button(bar, text="Approve none", command=lambda: self._set_all(False),
                   style="Ghost.TButton").pack(side="left", padx=4)
        ttk.Label(bar, text="Min confidence:").pack(side="left", padx=(12, 2))
        self.var_minconf = tk.DoubleVar(value=0.5)
        ttk.Spinbox(bar, from_=0.0, to=1.0, increment=0.05, width=5,
                    textvariable=self.var_minconf,
                    command=self._apply_confidence_filter).pack(side="left")

    def _build_body(self) -> None:
        body = ttk.Frame(self, padding=(8, 0, 8, 0))
        body.pack(fill="both", expand=True)

        cols = ("ok", "conf", "folder", "artist", "date", "album", "tracks")
        self.tv = ttk.Treeview(body, columns=cols, show="headings",
                               selectmode="browse")
        heads = [("ok", "✓", 32), ("conf", "Conf", 50), ("folder", "Folder", 200),
                 ("artist", "Artist", 150), ("date", "Date", 90),
                 ("album", "Proposed album", 320), ("tracks", "Trk", 50)]
        for key, label, w in heads:
            self.tv.heading(key, text=label, anchor="w")
            self.tv.column(key, width=w, minwidth=30,
                           stretch=(key in ("album", "folder")),
                           anchor="center" if key in ("ok", "conf", "tracks") else "w")
        self.tv.tag_configure("approved", foreground=_t.LOG_OK)
        self.tv.tag_configure("skipped",  foreground=_t.FG_DIM)
        self.tv.tag_configure("lowconf",  foreground=_t.LOG_WARN)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tv.pack(fill="both", expand=True)
        self.tv.bind("<space>", self._toggle_selected)
        self.tv.bind("<Double-1>", self._toggle_selected)

    def _build_bottom(self) -> None:
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        ttk.Button(bar, text="Close", command=self.destroy).pack(side="right")
        self.btn_apply = ttk.Button(bar, text="Apply to approved",
                                    style="Action.TButton",
                                    command=self._apply, state="disabled")
        self.btn_apply.pack(side="right", padx=4)

    # ── Scan ──────────────────────────────────────────────────────────────────

    def _scan_dialog(self) -> None:
        d = filedialog.askdirectory(
            parent=self, title="Select library folder to scan",
            initialdir=self.config_obj.get("last_input_dir") or None)
        if not d:
            return
        root = Path(d)
        self.config_obj["last_input_dir"] = str(root)
        self.config_obj.save()
        for iid in self.tv.get_children():
            self.tv.delete(iid)
        self._concerts.clear()
        self._scanning = True
        self.status.configure(text=f"Scanning {root}…")
        ffprobe = get_tool("ffprobe").path(self.config_obj)
        ffprobe = ffprobe if ffprobe.exists() else None
        threading.Thread(target=self._scan_worker, args=(root, ffprobe),
                         daemon=True).start()

    def _scan_worker(self, root: Path, ffprobe) -> None:
        stubs = scan_library(root)
        self.after(0, lambda: self.status.configure(
            text=f"Found {len(stubs)} show folder(s). Inferring metadata…"))
        for stub in stubs:
            c = infer_concert(stub, ffprobe)
            self.after(0, lambda c=c: self._add_concert_row(c))
        self.after(0, self._scan_done)

    def _add_concert_row(self, c: ConcertFolder) -> None:
        self._concerts.append(c)
        conf = c.confidence()
        c.approved = conf >= self.var_minconf.get()
        self.tv.insert("", "end", iid=str(len(self._concerts) - 1), values=(
            "☑" if c.approved else "☐",
            f"{conf:.2f}",
            c.folder.name,
            c.artist or "—",
            c.date or "—",
            c.album_name() or "—",
            len(c.audio_files),
        ), tags=(self._row_tag(c, conf),))

    def _row_tag(self, c: ConcertFolder, conf: float) -> str:
        if not c.approved:
            return "skipped"
        return "approved" if conf >= 0.5 else "lowconf"

    def _scan_done(self) -> None:
        self._scanning = False
        n = len(self._concerts)
        n_app = sum(1 for c in self._concerts if c.approved)
        self.status.configure(text=f"{n} folders found, {n_app} approved. "
                                   "Space/double-click to toggle a row.")
        self.btn_apply.configure(state="normal" if n else "disabled")

    # ── Selection toggles ─────────────────────────────────────────────────────

    def _toggle_selected(self, _evt=None) -> str:
        sel = self.tv.selection()
        if not sel:
            return "break"
        idx = int(sel[0])
        c = self._concerts[idx]
        c.approved = not c.approved
        self._update_row(idx)
        return "break"

    def _set_all(self, approved: bool) -> None:
        for idx, c in enumerate(self._concerts):
            c.approved = approved
            self._update_row(idx)

    def _apply_confidence_filter(self) -> None:
        thresh = self.var_minconf.get()
        for idx, c in enumerate(self._concerts):
            c.approved = c.confidence() >= thresh
            self._update_row(idx)

    def _update_row(self, idx: int) -> None:
        c = self._concerts[idx]
        conf = c.confidence()
        vals = list(self.tv.item(str(idx), "values"))
        vals[0] = "☑" if c.approved else "☐"
        self.tv.item(str(idx), values=vals, tags=(self._row_tag(c, conf),))

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _apply(self) -> None:
        approved = [c for c in self._concerts if c.approved]
        if not approved:
            messagebox.showinfo("Bulk Tag Cleanup", "No folders are approved.")
            return
        if not mutagen_available():
            messagebox.showerror("Bulk Tag Cleanup",
                                 "mutagen is required. Install it or run the app installer.")
            return
        total_files = sum(len(c.audio_files) for c in approved)
        if not messagebox.askyesno(
                "Bulk Tag Cleanup",
                f"Write normalized tags to {total_files} file(s) "
                f"across {len(approved)} folder(s)?\n\nThis modifies the files in place."):
            return
        self.btn_apply.configure(state="disabled")
        self.progress.configure(maximum=len(approved), value=0)
        threading.Thread(target=self._apply_worker, args=(approved,),
                         daemon=True).start()

    def _apply_worker(self, approved: list[ConcertFolder]) -> None:
        total_ok, total_err = 0, []
        for i, c in enumerate(approved):
            self.after(0, lambda nm=c.folder.name, i=i:
                       self.status.configure(text=f"[{i+1}/{len(approved)}] {nm}"))
            ok, errs = apply_concert(c)
            total_ok += ok
            total_err.extend(errs)
            self.after(0, lambda v=i + 1: self.progress.configure(value=v))
        self.after(0, lambda: self._apply_done(total_ok, total_err, len(approved)))

    def _apply_done(self, ok: int, errors: list[str], n_folders: int) -> None:
        self.btn_apply.configure(state="normal")
        self.progress.configure(value=0)
        self.status.configure(text=f"✓ Tagged {ok} file(s) across {n_folders} folder(s), "
                                   f"{len(errors)} error(s).")
        if errors:
            messagebox.showerror("Bulk Tag Cleanup",
                                 f"Tagged {ok} file(s). {len(errors)} failed:\n\n"
                                 + "\n".join(errors[:10]))
        else:
            messagebox.showinfo("Bulk Tag Cleanup",
                                f"Done. Tagged {ok} file(s) across {n_folders} folder(s).")
