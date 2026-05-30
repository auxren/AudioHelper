# Contributing

Context for contributors (and AI agents) working on **Trader's Little Jedi** —
the Python 3.11+ / tkinter desktop app in `audiohelper/`.

> `Claude.md` is an old *aspirational* Electron design doc — ignore its tech
> stack and file paths. The real app is described in [README.md](README.md).

## Run & test
```bash
python3 TradersLittleJedi.py     # run from source (needs tkinter, mutagen, ffmpeg/flac)
bash run_tests.sh                # 72 headless pytest tests
bash build_mac.sh --skip-sign --skip-notarize   # build a local .app/.dmg
```
The dev virtual environment is `.venv/` (created by `install_mac.sh` or the
build scripts). If your system `python3` lacks `_tkinter`, use `.venv/bin/python3`.

## Architecture
- `audiohelper/app.py` — home screen (tool tiles) + main window. Each tile opens
  a dialog module.
- One module per tool: `show_splitter.py`, `jedi_tagger.py`, `bulk_tagger.py`,
  `batch_convert.py`, plus the original TLH tools (`encode_wav.py`, `decode.py`,
  `checksum_dialogs.py`, `torrent_dialogs.py`, `dsd_edit.py`, …).
- Shared logic: `live_tagger.py` (eTree parser + `read_text_smart`),
  `tc_tagger.py` (mutagen read/write), `tc_sources.py` (SBD/AUD/mic detection),
  `tools.py` (CLI tool discovery/auto-update), `theme.py` (dark theme),
  `config.py` (settings; writes to a user-data dir when frozen).

## Conventions & gotchas
- **Tag writing** goes through `tc_tagger.write_tags` (mutagen) — it preserves
  embedded FLAC PICTURE blocks. `tag_io.py` is the older ffmpeg path.
- **CLI tools**: `tools.Tool.path()` resolves override → bundled (Windows `.exe`
  only) → PATH (strips `.exe`, tries lowercase) → `/opt/homebrew` etc. Never
  hardcode tool paths.
- **Text files** (setlists, info.txt): always read with
  `live_tagger.read_text_smart` (UTF-8/16/cp1252) and skip ripper logs with
  `looks_like_log_file`.
- **tkinterdnd2** fails to load on Apple Silicon; `app.py` falls back to plain
  `tk.Tk` without double-initializing (avoids a past stray-window bug).
- **HighGrabber** (`audiohelper/highgrabber/`) is vendored but **intentionally
  hidden** — no GUI entry point, heavy deps kept out of `requirements.txt`. See
  its `VENDORED.md`.
- Add a `tests/` case for any parser/inference/tagging change and keep the suite
  green (`bash run_tests.sh`).

## Platform priority
Windows and macOS are the focus. Linux/Docker is on the roadmap but deferred.
The macOS build is verified working; the Windows build is wired but not yet
verified on a Windows machine.

## Commit style
Conventional, descriptive messages. Branch off `main` for non-trivial work.
Co-author trailer for AI-assisted commits.
