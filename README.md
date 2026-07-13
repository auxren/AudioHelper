# Trader's Little Jedi

A cross-platform desktop toolkit for live-music traders, tapers, and archivists.
It splits full-show recordings into tracks, tags them from eTree/furthur setlist
files, cleans up tags across a whole library, converts formats in bulk, and
handles the usual trader chores — checksums, torrents, integrity tests, and
ReplayGain — all from one dark-themed home screen.

Built in Python + tkinter. Modeled on Trader's Little Helper, with ideas borrowed
from XLD, XACT, and foo_tradersfriend.

---

## Install

The app launches from a **home screen of tool tiles** — no hunting through menus.

### macOS

**Option A — prebuilt app (easiest):**
Open `dist/TradersLittleJedi-mac.dmg` (after a build, see [BUILD.md](BUILD.md)),
drag **Trader's Little Jedi** to Applications, then right-click → **Open** the
first time (required for unsigned builds; a signed build opens normally).

**Option B — install script (sets up everything):**
```bash
bash install_mac.sh
```
Installs Homebrew (if needed), Python with tkinter, ffmpeg, and flac, creates a
virtual environment, and adds a launcher to your Desktop and `/Applications`.

> Requires macOS 12 Monterey or later.

### Windows

Double-click **`Install Windows.bat`**. It installs Python (via `winget` if
missing), the Python packages, and downloads ffmpeg + flac into `tools\`, then
creates a Desktop shortcut.

> Requires Windows 10 or 11.

### Run from source (developers)
```bash
# macOS / Linux
brew install python-tk ffmpeg flac        # macOS deps
pip3 install -r requirements.txt
python3 TradersLittleJedi.py
```
```bat
:: Windows
TradersLittleJedi.cmd
```

---

## The tools

The home screen has nine tiles. The first three are the heart of the
live-recording workflow.

### ✂ Show Splitter
Turn one full-show WAV/FLAC into individual, tagged tracks.
- Loads the recording and draws an **interactive waveform**. Navigate with the
  trackpad/mouse: **scroll = zoom**, **shift-scroll = pan**, **click/drag = scrub**
  the playhead (plus +/−/Fit buttons).
- **▶ Play** / **Spacebar** previews from the cursor through your chosen **audio
  output device** (handy when your DAC isn't the system default). Click any track
  row to **audition** it from that marker.
- Place track boundaries three ways: **drag the orange markers**, click **Detect
  silences** (adaptive envelope detection that finds the gaps between songs in a
  live recording), or **Load setlist…** to pull titles from an eTree `.txt`.
  Tracks always stay ordered by start time.
- **Filename template** with eTree-style tokens (default `%a%dd%Dt%n` →
  `alo2026-05-24d1t01.flac`): `%a` artist abbrev · `%d` date · `%D` set/disc ·
  `%n` track # · `%t` title. A **?** button explains them; a live preview shows
  the result. The artist abbreviation is auto-guessed (gd, ph, bruce, glaf…).
- **Save session… / Load session…** — store the markers, titles, metadata, and
  template in a small, hand-editable `.tljsplit` text file you can reopen and
  refine later.
- **Split & Package** — one button takes the tape to a share-ready folder. It
  writes one FLAC/WAV/MP3 per track (fully tagged; album name
  `DATE Venue, City, State`, e.g. `2026-05-24 HopMonk Tavern, Novato, CA`), then
  optionally adds the **setlist .txt**, **FFP/MD5 checksums**, a **cover image**
  (copied in and embedded in each FLAC), and a private **.torrent** — and can
  hand the folder straight to the **Live Music Archive upload** (below).

> **Complete tape → distribution flow:** load tape → load setlist → place markers
> → **Split & Package**. Out comes a folder with correctly-named tagged FLACs, an
> FFP checksum, the cover image, the eTree setlist `.txt`, and a torrent —
> everything a tracker or the LMA expects.

### ♪ Live Show Tagger
Tag one show, foo_tradersfriend-style but cross-platform.
- **Add folder** (recurses into CD1/CD2 subfolders) auto-loads the audio and
  auto-detects the setlist `.txt`.
- Multi-select (click, shift-click, ⌘/Ctrl-click, ⌘/Ctrl-A) for bulk actions.
- Edit Artist / Date / Venue / Location / Source with per-field Revert.
- **File Tags** tab shows the standard mp3tag fields for the selected file so you
  can see exactly what's there (or that it's untagged).
- **Cover Art** tab previews the embedded cover and preserves it on save.
- **Apply tags** writes FLAC, MP3, WAV, AIFF, M4A, Ogg, and Opus.
- Recording source (SBD/AUD/FM, mic models like Schoeps MK4 or AKG 414) is
  detected automatically from the Source text.

### ⊛ Bulk Tag Cleanup
Scan a whole library and normalize every show's tags at once.
- Recursively finds show folders; **merges CD1/CD2 disc subfolders** into one
  concert with correct disc numbers.
- Infers Artist / Date / Venue / City / Source from setlist files, existing tags,
  and folder names — skipping EAC/foobar/XLD **ripper logs** and handling
  UTF-16/Windows-1252 encodings.
- Review table with a **confidence score**; the **Proposed Artist** and
  **Proposed Album** cells are **editable inline** so you can bulk-fix anything
  the heuristics get wrong before writing.
- Builds canonical album names: `YYYY-MM-DD Venue, City, Region [SBD/mics]`.

### The rest
| Tile | What it does |
|---|---|
| ⇄ **Batch Convert** | Convert many files/folders with saved presets — FLAC, WAV, MP3 (VBR/CBR/ABR), AAC, Ogg, AIFF. Post-conversion test, MD5, delete-source. |
| ✎ **Audio Editor** | Waveform editor — trim, split, gapless merge. |
| ✓ **Checksums** | Create & verify MD5, FFP, SFV, CFP, ST5. |
| ⬡ **Torrents** | Create and verify `.torrent` files. |
| ◎ **Analysis** | File details (MediaInfo), integrity tests, SBE check, MPEG-in-WAV detection, ReplayGain. |
| ⊞ **More Tools** | DSD editor/trim, batch rename, strip/repair headers, convert, fix SBEs, **upload to Archive.org (LMA)**, update CLI tools. |

**⚙ Settings** (top-right) sets default formats, compression levels, output
folders, and other preferences, persisted across sessions.

### Upload to the Live Music Archive
Either via the Show Splitter's **Upload to Archive.org (LMA) after** checkbox or
**More Tools → Upload folder to Archive.org (LMA)…**, a finished folder can be
uploaded to archive.org's `etree` collection:
- Builds the etree metadata from the show data and suggests an LMA-style
  identifier (`alo2026-05-24.schoepsmk21.flac16`, editable).
- Uses your archive.org **S3 keys** (archive.org/account/s3.php), optionally
  remembered locally. Uploads the FLACs, `.txt`, and checksums (archive.org
  generates its own derivatives/torrent).
- Shows the public URL and asks for **explicit confirmation** before posting.

> Writing to the LMA (`etree`) collection requires being an **approved LMA
> uploader**, and the band must be trade-friendly. The app performs the upload;
> it can't grant LMA approval. Requires the `internetarchive` package (bundled in
> the app; `pip install internetarchive` from source).

---

## Setlist (eTree) format

The tagger and splitter read plain-text setlist/info files. The parser is
deliberately forgiving and handles:
- **Key-value** headers (`Artist:`, `Date:`, `Venue:`, …)
- **Positional** headers (artist / date / venue on separate lines)
- **Embedded-date** headers (`The Rolling Stones 1999 06 08 Shepherds Bush…`)
- **Numbered** setlists (`1. Song`) and **unnumbered** ones (bare titles under a
  `Set 1:` / `Disc 2:` / `Encore:` header)
- **Multi-disc** shows (`Disc 1:` / `CD 2:`) with continuous track numbering
- **US-format dates** (`ALO 5/24/26`) and **repeated header blocks** between sets
- **Footnotes** (`*ASHER`, `LAUNDRY*`) folded into notes / stripped from titles
- UTF-8, UTF-16 (BOM or not), and Windows-1252 encodings
- Trailing lineup/source/notes after the setlist

Ripper/player logs (EAC, foobar2000, XLD, dBpoweramp) are detected and ignored
so they're never mistaken for a setlist.

## Session files (`.tljsplit`)

The Show Splitter can save/restore a session — the audio path, show metadata,
output format, filename template, and every track marker — as a small,
human-editable text file:
```
source   = /Tapes/2026-05-24/.../alo...caf
artist   = ALO
date     = 2026-05-24
abbrev   = alo
template = %a%dd%Dt%n

[tracks]
      0:00 | 1 | KC Intro
    4:19.2 | 1 | Animal Liberation 1 & 2
   11:38.3 | 1 | Blank Canvas
```
Edit times/titles in any text editor and reload, or hand it to someone else to
finish.

---

## CLI tools

| Tool | Used for | macOS | Windows |
|---|---|---|---|
| ffmpeg / ffprobe | decode, convert, tag, probe, waveform, silence detect | Homebrew | bundled in `tools\` |
| flac / metaflac | encode/decode/test/tag FLAC | Homebrew | bundled |
| lame | MP3 encoding | Homebrew | bundled |
| mediainfo | detailed file info (optional) | `brew install media-info` | auto-download |

On Windows the app resolves tools from `tools\` first; on macOS/Linux it uses
your system PATH (Homebrew). **Tools → Update all CLI tools…** manages them.

---

## Building distributable apps

See **[BUILD.md](BUILD.md)** for producing a signed/notarized macOS `.app`/`.dmg`
and a Windows `.exe` installer via PyInstaller.

---

## Development

```bash
bash run_tests.sh          # 80 headless tests (pytest)
pip install -r requirements-dev.txt
```
Tests cover the eTree parser, encoding/log detection, source detection, bulk
inference, tag read/write, tool-path resolution, filename templates, session
round-trips, and the silence detector. Audio-touching tests skip automatically
when ffmpeg/mutagen aren't present.

Project layout:
```
audiohelper/            the app package (one module per tool dialog)
  app.py                home screen + window
  theme.py              dark theme (color tokens, ttk styles, log coloring)
  show_splitter.py      Show Splitter (waveform view)
  jedi_tagger.py        Live Show Tagger
  bulk_tagger.py        Bulk Tag Cleanup
  batch_convert.py      Batch Converter
  live_tagger.py        eTree parser + setlist helpers
  tc_tagger.py          mutagen tag read/write
  tc_sources.py         SBD/AUD/FM + mic detection
  tools.py              CLI tool discovery / auto-update
tests/                  pytest suite
assets/                 icon generator + generated icons
installer/              Inno Setup script (Windows)
TradersLittleJedi.py    entry point
TradersLittleJedi.spec  PyInstaller spec
```

---

## Requirements

- Python 3.11+
- **mutagen** — tag read/write (FLAC, MP3, WAV, AIFF, M4A, Ogg, Opus)
- **tkinterdnd2** — drag-and-drop (optional; the app runs without it)
- **Pillow** — cover-art previews and icon generation (optional)
- **internetarchive** — Live Music Archive upload (optional; bundled in the app)
- ffmpeg, ffprobe, flac, metaflac, lame on PATH (or bundled on Windows)

---

## License

Trader's Little Jedi is licensed under the
[**PolyForm Noncommercial License 1.0.0**](https://polyformproject.org/licenses/noncommercial/1.0.0/).

You are free to **use, copy, modify, fork, and share** it for any
**non-commercial** purpose. **Commercial use is not permitted.** See
[LICENSE](LICENSE) for the full text.

---

## Credits

Trader's Little Jedi began as a fork of **[kskreider](https://github.com/kskreider)'s
Audio Helper**, and owes its foundation to his work. The original project's
structure, the TLH-style toolset (encode/decode, checksums, torrents, SBE
fixing, analysis), and much of the core design came from him. Huge thanks to
**kskreider** for building that base and sharing it — this project wouldn't
exist without it.

Additional building blocks: tag inference and source/mic detection adapted from
[TagCleaner](https://github.com/auxren/TagCleaner); UI/design cues from
ConcertTagger and HighGrabber. Inspired by Trader's Little Helper, XLD, XACT,
and foo_tradersfriend.
