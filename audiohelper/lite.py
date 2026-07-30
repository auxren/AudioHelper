"""Show Chopper — the stripped-down, wook-proof chopping flow.

One window, one big button. Drop the raw set files, paste the setlist
from your phone (phish.net / etree / one-song-per-line all work), hit
CHOP. Out comes a tagged FLAC folder with an info txt and checksums,
plus a plain-English list of any cuts that deserve a listen in the full
Show Splitter. Runs entirely offline; if faster-whisper is installed the
proposals get lyric-anchored, otherwise envelope analysis alone.

Launch: the "Show Chopper" tile in the app, or  python -m audiohelper --lite
"""

from __future__ import annotations

import array
import re
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .auto_align import SetlistEntry, align, envelope_db, find_dips
from .checksums import flac_fingerprint, md5sum, write_ffp, write_md5
from .live_tagger import EtreeSet, EtreeShow, EtreeTrack, generate_etree_file
from .tc_tagger import mutagen_available, write_tags
from .tools import get_tool

AUDIO_EXTS = {".wav", ".flac", ".aif", ".aiff", ".caf"}
_DATE_RE = re.compile(r"(\d{4})[-._](\d{2})[-._](\d{2})")
_SET_HDR = re.compile(r"^(set\s*\d+|encore\s*\d*|e\d*)\s*:?\s*", re.IGNORECASE)
_NUMBERED = re.compile(r"^\d+[\.\)]\s+")


# ── Pure helpers (unit-tested) ────────────────────────────────────────────────

def guess_date(paths: list[str]) -> str:
    """Find a YYYY-MM-DD anywhere in the given paths."""
    for p in paths:
        m = _DATE_RE.search(str(p))
        if m:
            return "-".join(m.groups())
    return ""


def parse_loose_setlist(text: str) -> list[tuple[str, list[tuple[str, bool]]]]:
    """Parse a pasted setlist into [(set_label, [(title, segues_next)])].

    Accepts the formats a phone paste actually produces:
    - phish.net style paragraphs:  "Set 1: Tweezer > Prince Caspian, Sand"
    - etree numbered lines under "Set 1:" headers
    - bare one-song-per-line (becomes a single "Set 1")
    A ">" between songs (or trailing a title) marks a segue.
    """
    sets: list[tuple[str, list[tuple[str, bool]]]] = []
    cur: list[tuple[str, bool]] = []
    cur_label = ""

    def close():
        nonlocal cur, cur_label
        if cur:
            sets.append((cur_label or f"Set {len(sets) + 1}", cur))
        cur, cur_label = [], ""

    def add_run(run: str):
        """Split one line/paragraph of songs on commas and '>'."""
        # Normalize '->' to '>' and split, keeping the separators.
        run = run.replace("->", ">")
        parts = re.split(r"([>,])", run)
        pending: str | None = None
        for tok in parts:
            tok = tok.strip()
            if tok == ">":
                if pending is None and cur:
                    # '>' between lines: mark previous track as segue
                    t, _ = cur[-1]
                    cur[-1] = (t, True)
                elif pending:
                    cur.append((pending, True))
                    pending = None
                continue
            if tok == ",":
                if pending:
                    cur.append((pending, False))
                    pending = None
                continue
            if tok:
                if pending:
                    cur.append((pending, False))
                pending = tok
        if pending:
            segue = pending.endswith(">")
            cur.append((pending.rstrip(" >"), segue))

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        m = _SET_HDR.match(line)
        if m:
            close()
            cur_label = m.group(1).title().replace("  ", " ")
            rest = line[m.end():].strip()
            if rest:
                add_run(rest)
            continue
        if _NUMBERED.match(line):
            add_run(_NUMBERED.sub("", line))
            continue
        # Skip obvious non-song metadata lines.
        if re.match(r"^(artist|date|venue|location|source|taper|notes)\s*:",
                    line, re.IGNORECASE):
            continue
        add_run(line)
    close()
    return sets


def plan_output_name(artist: str, date: str) -> str:
    """Folder / file prefix: 'ph2026-07-28' style."""
    abbrev = "".join(w[0] for w in artist.lower().split()[:3]) or "show"
    known = {"phish": "ph", "widespread panic": "wsp", "grateful dead": "gd",
             "billy strings": "bs", "goose": "goose"}
    return known.get(artist.strip().lower(), abbrev) + date


# ── Headless chop pipeline ────────────────────────────────────────────────────

def _decode_envelope(ffmpeg: Path, path: Path) -> tuple[list[float], float]:
    """Return (normalized |samples| at 4 kHz mono, duration_sec)."""
    p = subprocess.run(
        [str(ffmpeg), "-v", "quiet", "-i", str(path), "-af", "aresample=4000",
         "-ac", "1", "-f", "s16le", "-"],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    buf = array.array("h")
    buf.frombytes(p.stdout[:len(p.stdout) - len(p.stdout) % 2])
    if not buf:
        return [], 0.0
    peak = max(abs(v) for v in buf) or 1
    return [abs(v) / peak for v in buf], len(buf) / 4000.0


def _transcribe(path: Path, status) -> list[tuple[float, float, str]]:
    """Local Whisper pass; empty list if faster-whisper isn't installed."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        status("faster-whisper not installed — chopping by ear (envelope) only.")
        return []
    try:
        status("Listening for lyrics… (first run downloads a ~460 MB model)")
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = model.transcribe(str(path), language="en", beam_size=1,
                                   condition_on_previous_text=False)
        out = []
        for s in segs:
            out.append((s.start, s.end, s.text))
            status(f"Listening… {int(s.end // 60)} min in")
        return out
    except Exception:
        return []


def chop_show(files: list[Path], sets, meta: dict, outdir: Path,
              status) -> dict:
    """Run the whole pipeline. Returns a summary dict.

    files: one audio file per set, in order. sets: parse_loose_setlist()
    output (empty → auto-detect gaps, generic titles). meta: artist/date/
    venue/location strings. status: callable(str) for progress lines.
    """
    ffmpeg = _tool_path("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found — open the main app once so it "
                           "can install its tools, then retry.")
    outdir.mkdir(parents=True, exist_ok=True)
    prefix = plan_output_name(meta.get("artist", ""), meta.get("date", ""))
    flagged: list[str] = []
    written: list[Path] = []
    etree_sets: list[EtreeSet] = []
    gi = 0
    n_sets = max(len(files), 1)

    for si, src in enumerate(files, 1):
        label, entries = (sets[si - 1] if si <= len(sets)
                          else (f"Set {si}", []))
        status(f"Analyzing {src.name}…")
        samples, dur = _decode_envelope(ffmpeg, src)
        if not samples:
            raise RuntimeError(f"Could not decode {src.name}")
        env, frame = envelope_db(samples, dur)
        dips = find_dips(env, frame)
        if not entries:
            entries = [(f"Track {i + 1:02d}", False)
                       for i in range(len(dips) + 1)]
        setlist = [SetlistEntry(t, seg) for t, seg in entries]
        transcript = _transcribe(src, status) if len(setlist) > 1 else []
        bounds = align(setlist, dips, transcript, dur)
        starts = [0.0] + [b.time for b in bounds]
        ends = [b.time for b in bounds] + [None]

        et_tracks: list[EtreeTrack] = []
        for ti, ((title, segue), t0, t1) in enumerate(zip(entries, starts,
                                                          ends), 1):
            gi += 1
            name = f"{prefix}s{si}t{ti:02d}.flac"
            out = outdir / name
            status(f"Cutting {name}  ({title})")
            cmd = [str(ffmpeg), "-v", "error", "-y", "-i", str(src),
                   "-ss", str(t0)]
            if t1 is not None:
                cmd += ["-to", str(t1)]
            cmd += ["-map_metadata", "-1", "-c:a", "flac",
                    "-compression_level", "8", str(out)]
            subprocess.run(cmd, check=True, capture_output=True,
                           creationflags=getattr(subprocess,
                                                 "CREATE_NO_WINDOW", 0))
            display = title + (" >" if segue else "")
            if mutagen_available():
                write_tags(out, {
                    "ARTIST": meta.get("artist", ""),
                    "ALBUM": f"{meta.get('date', '')} "
                             f"{meta.get('venue', '')}".strip(),
                    "DATE": meta.get("date", ""),
                    "TITLE": display,
                    "TRACKNUMBER": f"{ti:02d}",
                    "TRACKTOTAL": str(len(entries)),
                    "DISCNUMBER": str(si),
                    "DISCTOTAL": str(n_sets),
                    "VENUE": meta.get("venue", ""),
                    "LOCATION": meta.get("location", ""),
                })
            written.append(out)
            et_tracks.append(EtreeTrack(global_index=gi, set_index=ti,
                                        title=display))
        for b in bounds:
            if b.confidence < 0.4:
                a = entries[b.index][0]
                z = entries[b.index + 1][0]
                flagged.append(f"Set {si}: {a} → {z}  "
                               f"(~{int(b.time // 60)}:{int(b.time % 60):02d})")
        etree_sets.append(EtreeSet(label=label, tracks=et_tracks))

    status("Writing info file and checksums…")
    show = EtreeShow(artist=meta.get("artist", ""), date=meta.get("date", ""),
                     venue=meta.get("venue", ""),
                     location=meta.get("location", ""),
                     source=meta.get("source", "") or
                     "AUD [add your rig / source chain]",
                     sets=etree_sets)
    (outdir / f"{prefix}.txt").write_text(generate_etree_file(show),
                                          encoding="utf-8")
    write_md5([(p.name, md5sum(p)) for p in written], outdir / f"{prefix}.md5")
    metaflac = _tool_path("metaflac")
    if metaflac:
        write_ffp([(p.name, flac_fingerprint(p, metaflac)) for p in written],
                  outdir / f"{prefix}.ffp")
    return {"tracks": len(written), "outdir": outdir, "flagged": flagged,
            "prefix": prefix}


def _tool_path(name: str):
    try:
        from .config import Config
        p = get_tool(name).path(Config())
        if p and Path(p).exists():
            return Path(p)
    except Exception:
        pass
    from shutil import which
    w = which(name)
    return Path(w) if w else None


# ── The one-button window ─────────────────────────────────────────────────────

class LiteWindow:
    """Show Chopper window. Root when standalone, Toplevel from the app."""

    def __init__(self, parent=None):
        self.win = tk.Toplevel(parent) if parent else tk.Tk()
        self.win.title("Show Chopper")
        self.win.geometry("620x640")
        self.files: list[Path] = []
        f = ttk.Frame(self.win, padding=16)
        f.pack(fill="both", expand=True)

        ttk.Label(f, text="1. Throw in your raw files (one per set, in order)",
                  font=("Helvetica", 14, "bold")).pack(anchor="w")
        self.lbl_files = ttk.Label(f, text="No files yet.", foreground="gray")
        self.lbl_files.pack(anchor="w", pady=(2, 4))
        ttk.Button(f, text="Add raw files…", command=self._add_files
                   ).pack(anchor="w", pady=(0, 12))

        ttk.Label(f, text="2. Paste the setlist (phish.net paste is fine — "
                          "or leave empty)",
                  font=("Helvetica", 14, "bold")).pack(anchor="w")
        self.txt = tk.Text(f, height=8, wrap="word")
        self.txt.pack(fill="both", expand=True, pady=(4, 12))

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(0, 12))
        self.vars = {}
        for i, key in enumerate(("Artist", "Date", "Venue")):
            ttk.Label(row, text=key).grid(row=0, column=2 * i, padx=(0, 4))
            v = tk.StringVar()
            self.vars[key.lower()] = v
            ttk.Entry(row, textvariable=v, width=14).grid(row=0,
                                                          column=2 * i + 1,
                                                          padx=(0, 10))

        self.btn = tk.Button(f, text="CHOP MY SHOW", font=("Helvetica", 20,
                                                           "bold"),
                             height=2, command=self._go)
        self.btn.pack(fill="x")
        self.status = ttk.Label(f, text="", wraplength=580, justify="left")
        self.status.pack(anchor="w", pady=(10, 0))

    def _add_files(self):
        picks = filedialog.askopenfilenames(
            parent=self.win, title="Raw set files (in show order)",
            filetypes=[("Audio", "*.wav *.flac *.aif *.aiff *.caf"),
                       ("All files", "*.*")])
        for p in picks:
            if Path(p).suffix.lower() in AUDIO_EXTS:
                self.files.append(Path(p))
        self.lbl_files.configure(
            text=" · ".join(p.name for p in self.files) or "No files yet.")
        if self.files and not self.vars["date"].get():
            self.vars["date"].set(guess_date([str(p) for p in self.files]))

    def _set_status(self, msg: str):
        self.win.after(0, lambda: self.status.configure(text=msg))

    def _go(self):
        if not self.files:
            self.status.configure(text="Add your raw files first.")
            return
        sets = parse_loose_setlist(self.txt.get("1.0", "end"))
        if sets and len(sets) != len(self.files):
            self.status.configure(
                text=f"You gave {len(self.files)} file(s) but the setlist has "
                     f"{len(sets)} set(s). One file per set, in order — or "
                     "clear the setlist to auto-chop.")
            return
        meta = {k: v.get().strip() for k, v in self.vars.items()}
        meta["location"] = ""
        outdir = self.files[0].parent / plan_output_name(
            meta.get("artist", ""), meta.get("date", ""))
        self.btn.configure(state="disabled", text="CHOPPING…")

        def worker():
            try:
                r = chop_show(self.files, sets, meta, outdir, self._set_status)
                lines = [f"DONE. {r['tracks']} tracks in {r['outdir'].name}/ "
                         "with info txt + checksums."]
                if r["flagged"]:
                    lines.append("")
                    lines.append("Give these cuts a quick listen in the Show "
                                 "Splitter (Review cuts) before uploading:")
                    lines += [f"  ⚠ {x}" for x in r["flagged"]]
                else:
                    lines.append("Every cut looks clean. Ship it.")
                self._set_status("\n".join(lines))
            except Exception as e:
                self._set_status(f"Something broke: {e}")
            finally:
                self.win.after(0, lambda: self.btn.configure(
                    state="normal", text="CHOP MY SHOW"))

        threading.Thread(target=worker, daemon=True).start()


def main() -> None:
    LiteWindow().win.mainloop()
