# Trader's Little Jedi

Audio toolkit for live music traders and tapers — encoding, checksums, torrents, and a full **Live Show Tagger** with eTree/furthur-style setlist support.

---

## Install

### Windows

1. Download and unzip this repository
2. Double-click **`Install Windows.bat`**
3. Follow the prompts — Python, ffmpeg, flac, and all dependencies are installed automatically
4. A shortcut appears on your Desktop when done

> Requires Windows 10 or 11. The installer uses `winget` (built into Windows) and downloads ~90 MB of CLI tools on first run.

---

### macOS

1. Download and unzip this repository
2. Open Terminal, `cd` to the AudioHelper folder, and run:
   ```bash
   bash install_mac.sh
   ```
3. Follow the prompts — Homebrew, Python with tkinter, ffmpeg, and flac are installed automatically
4. A launcher appears on your Desktop and in `/Applications` when done

> Requires macOS 12 Monterey or later.

---

## What it does

| Feature | Menu |
|---|---|
| Encode WAV → FLAC / APE / MP3 / SHN | Format |
| Re-encode, decode, convert formats | Format |
| Gapless merge, audio editor / splitter | Format |
| Create & verify checksums (MD5, FFP, SFV, CFP, ST5) | Checksum |
| Create & verify torrent files | Torrent |
| Show file details (MediaInfo), test integrity | Analysis |
| Check & fix sector boundary errors | Analysis / Tools |
| Test WAV files for hidden MP3 source | Analysis |
| ReplayGain scan | Analysis |
| **Live Show Tagger** — tag from eTree text files | Tools |
| **Show Splitter** — split a full-show WAV into tracks | Tools |
| Batch rename | Tools |
| Strip / repair audio file headers | Tools |

---

## Show Splitter

The Show Splitter lets you take a single WAV or FLAC recording of an entire show and cut it into individual tracks:

1. **Tools → Show Splitter (WAV → tracks)…**
2. Load your full-show WAV or FLAC
3. Load the eTree setlist `.txt` file — track titles populate automatically
4. Click **Detect silences** — the app finds quiet gaps between songs and proposes split points
5. Review and adjust start times by clicking any row in the track list
6. Choose output folder and format (FLAC / WAV / MP3)
7. Click **Split & Tag** — individual files are written and tagged with Artist, Date, Venue, Source, Title, and track numbers

---

## Live Show Tagger

Equivalent to foo_tradersfriend / Live Show Tagger but built-in and cross-platform:

1. **Tools → Live Show Tagger…**
2. **Add folder** — loads all audio files in a show folder, auto-sorted
3. The setlist `.txt` is auto-detected if it's in the same folder, or load it manually
4. Edit Artist, Date, Venue, Location, Source as needed (Revert button restores originals)
5. **Apply tags** — writes all tags in one pass; FLAC, MP3, WAV, M4A, and Ogg supported

Source detection (SBD / AUD / FM, mic models like Schoeps MK4 or AKG 414) is automatic from the Source field text.

---

## Manual launch (no installer)

```bash
# Mac / Linux — install deps first
brew install python-tk ffmpeg flac          # Mac
pip3 install mutagen tkinterdnd2

# Run
python3 TradersLittleJedi.py
```

```bat
:: Windows — install deps first, then:
TradersLittleJedi.cmd
```

---

## CLI tools

The app uses bundled CLI tools on Windows (`tools\` folder) and system tools on Mac (via Homebrew). To update bundled tools: **Tools → Update all CLI tools…**

| Tool | Used for |
|---|---|
| ffmpeg / ffprobe | Decode, convert, tag, probe audio files |
| flac / metaflac | Encode, decode, test, tag FLAC files |
| lame | MP3 encoding |

---

## Requirements

- Python 3.11+
- `mutagen` — tag writing for FLAC, MP3, WAV, M4A, Ogg
- `tkinterdnd2` — drag-and-drop support (optional; app works without it)
