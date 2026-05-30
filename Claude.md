# Hi-Res Jedi: Combined Audio Tool — Development Plan

## Executive Summary

**Hi-Res Trader** is a modern, cross-platform desktop application that evolves Trader's Little Helper into a complete high-resolution audio management workstation. It merges three distinct tools into one unified front end:

- **Trader's Little Helper (TLH)** — the structural and UX model: batch encoding/decoding, checksum creation/verification, format testing, torrent management, and Windows Explorer integration.
- **CD Wave Editor** — visual waveform-based track splitting for WAV, FLAC, APE, and other lossless formats.
- **TASCAM Hi-Res Editor** — native DSD (DSF/DSDIFF) file playback, trimming, joining, and export/conversion up to 11.2 MHz DSD and 384 kHz / 32-bit PCM.

The result is a single, cohesive tool for audiophiles, archivists, music traders, and recording engineers who work with high-resolution and lossless audio — from tapers sharing SHN/FLAC sets to mastering engineers handling DSD masters.

---

## Combined Feature Matrix

### From Trader's Little Helper (retained and modernized)
- Encode WAV → FLAC, APE, SHN, MP3
- Re-encode FLAC → FLAC (re-compression / metadata update)
- Decode FLAC, APE, MKW, SHN, MP2, MP3 → WAV
- Direct conversion: APE/FLAC/MKW/SHN → FLAC or MP3
- Test audio file integrity: FLAC, APE, SHN, MKW
- Verify checksums: CFP, FFP, MD5, SFV, ST5
- Create checksums: CFP, FFP, MD5, SFV, ST5
- Display audio file properties
- Fix sector boundary errors (FLAC, APE, SHN, MKW, WAV, MP3)
- Remove extra RIFF chunks
- Rewrite WAVE headers to canonical format
- Create SKT files for non-seekable SHN files
- Test WAV files for MP3 source (lossy-in-lossless detection)
- Create and inspect torrent files
- Hash torrents against local filesets
- Drag & drop for all supported file types
- Windows Explorer context menu integration
- Check for updates

### From CD Wave Editor (new module)
- Visual dual-pane waveform display (overview + zoomed)
- Interactive marker/split-point placement via mouse
- Playback-while-splitting workflow
- Named track list with per-track enable/disable
- Batch split export to individual files
- Support for WAV, FLAC, APE, SHN input

### From TASCAM Hi-Res Editor (new module)
- Native DSD (DSF / DSDIFF) file playback — no PCM downconversion
- Support for DSD rates: 2.8, 5.6, 11.2 MHz
- Support for PCM: 44.1–384 kHz, 16/24/32-bit
- Waveform display with in-point / out-point trim editing
- File join/combine (head + tail → new DSD or WAV)
- Export/convert: DSD ↔ PCM, frequency up/downsampling
- Apply short fade on export
- ASIO driver support (Windows) / CoreAudio (Mac)
- File information display (format, sample rate, bit depth, channel count, duration)

### New: Live Show Tagger / Setlist Metadata Module
- Load, parse, and edit eTree/furthur-style text files
- Bulk-tag audio files (FLAC, MP3, APE, WAV) with Artist, Date, Venue, Location, Source, Set, Track Title, Track Number
- Match audio files to setlist entries automatically by track order
- Edit setlist inline with multi-set support (Set 1, Set 2, Encore)
- Generate a .txt file named after the parent folder in the standard eTree text format
- Revert individual fields to original tag values
- Per-file match status display (matched / unmatched)

### New: Binary Tool Auto-Updater
- Checks for new versions of bundled CLI tools on a 7-day / 10-launch schedule
- Sources: GitHub Releases API (ffmpeg, flac), vendor pages for others
- Downloads to a staging area, verifies integrity, replaces on next restart
- Per-tool update log in Settings

### Net-New Enhancements
- Unified drag-and-drop queue across all modules
- Per-file routing: automatically detect format and route to appropriate module
- Background batch processing with live progress indicators
- Modern dark/light theme
- macOS and Windows 10/11 native feel
- Settings persistence (last-used folders, preferred codec settings, checksum types)
- Logging panel with exportable session log
- Optional WASAPI Exclusive / ASIO output for bit-perfect DSD/PCM playback

---

## Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| App shell | **Electron 31+** | Cross-platform (Win/Mac), Node.js backend, web UI, system tray, Explorer integration |
| UI framework | **React 18 + Vite** | Fast HMR, component model ideal for tabbed/panel layout |
| Styling | **Tailwind CSS + shadcn/ui** | Rapid dark-mode-first UI, accessible components |
| Waveform rendering | **wavesurfer.js** | GPU-accelerated canvas waveform, region/marker support, FLAC/WAV decode via Web Audio API |
| Audio playback | **node-audioplayer / Electron Web Audio** | For standard PCM; DSD playback via native ASIO bridge |
| DSD / native audio bridge | **ffmpeg (bundled static)** | Handles DSF/DSDIFF → PCM for waveform preview and export. Free, open source, no licensing issues. See DSD note below. |
| Format conversion | **ffmpeg** (static binary, bundled) | WAV, FLAC, MP3, APE proxy via ffmpeg; DSD export handled separately |
| FLAC CLI | **flac** (bundled binary) | Test, encode, decode, re-encode with full tag preservation |
| APE / SHN | **mac** (Monkey's Audio CLI), **shorten** (bundled) | Encode/decode APE and SHN |
| Audio tagging | **node-taglib2** (FLAC/MP3/APE/WAV via TagLib) + **metaflac** CLI for FLAC Vorbis comments | Unified tag read/write across all supported formats |
| Checksum engine | **Node.js crypto** + **md5**, **sfv** npm packages | MD5, SFV in-process; CFP/FFP/ST5 via external tools or custom implementation |
| Sector-boundary / RIFF fix | Custom Node.js binary parser | Replicate TLH's sector-fix and RIFF-rewrite logic natively |
| Torrent | **parse-torrent**, **create-torrent** npm packages | Create and inspect .torrent files |
| Binary auto-updater | **Custom updater module** using GitHub Releases API + node-fetch | Per-tool update checks on 7-day / 10-launch cadence |
| Build/package | **electron-builder** | NSIS installer (Win), DMG (Mac), auto-update support |

### A Note on Xrecode vs. ffmpeg for DSD

Xrecode3 offers an excellent GUI and a capable CLI for DSD conversion, but the CLI version costs $10 — incompatible with a free, open-source distribution model. The GUI is free but cannot be silently invoked by another application.

**Decision: use ffmpeg for all DSD work in Hi-Res Trader.**

ffmpeg's native DSD support is comprehensive: it reads DSF and DSDIFF files directly via libavcodec's DSD decoder and converts to PCM using the SoX resampler (soxr) for high-quality decimation. The command `ffmpeg -i input.dsf -af aresample=resampler=soxr -ar 176400 -c:a pcm_s24le output.wav` produces broadcast-quality 24-bit / 176.4 kHz PCM from a 2.8 MHz DSD64 source — equivalent to what Xrecode produces internally.

For users who want bit-perfect native DSD output (DoP or ASIO), that remains a v2 ASIO native addon feature. For SACD ISO extraction (`.iso` → individual DSD tracks), optionally bundle **sacd_extract** (free, BSD license, available at https://github.com/sacd-ripper/sacd-ripper), which is the only widely used free tool for that specific task.

---

## Application Architecture

```
hi-res-trader/
├── electron/
│   ├── main.ts
│   ├── preload.ts
│   ├── audio-engine/
│   │   ├── dsd-bridge.ts
│   │   ├── ffmpeg-runner.ts
│   │   ├── flac-runner.ts
│   │   ├── ape-runner.ts
│   │   ├── shn-runner.ts
│   │   └── sector-fix.ts
│   ├── checksum/
│   │   ├── md5.ts  sfv.ts  ffp.ts  cfp.ts  st5.ts
│   ├── torrent/
│   │   ├── create.ts  inspect.ts  hash.ts
│   ├── tagger/
│   │   ├── etree-parser.ts       ← parse/generate eTree text files
│   │   ├── tag-writer.ts         ← write tags to FLAC/MP3/APE/WAV
│   │   └── setlist-matcher.ts    ← match audio files to setlist entries
│   ├── updater/
│   │   ├── binary-updater.ts     ← per-tool update checker
│   │   ├── sources.ts            ← update source registry (URLs, APIs)
│   │   └── launch-counter.ts     ← persistent launch count for update schedule
│   └── updater.ts
├── src/
│   ├── App.tsx
│   ├── components/
│   │   ├── layout/
│   │   ├── queue/
│   │   ├── waveform/
│   │   ├── dsd/
│   │   ├── encoding/
│   │   ├── checksums/
│   │   ├── torrents/
│   │   ├── tagger/               ← Live Show Tagger module
│   │   │   ├── TaggerPanel.tsx
│   │   │   ├── TrackList.tsx
│   │   │   ├── SetlistEditor.tsx
│   │   │   ├── MetaDataFields.tsx
│   │   │   └── TextFileGenerator.tsx
│   │   └── settings/
│   │       └── SettingsPanel.tsx
│   └── ...
├── native/
├── binaries/win/  binaries/mac/
└── electron-builder.config.js
```

---

## Development Phases & Claude Code Prompts

---

### Phase 0 — Project Scaffolding

**Claude Code Prompt:**

```
Create a new Electron 31 + React 18 + Vite + TypeScript desktop application called "hi-res-trader".

Requirements:
- Use electron-vite as the build tool.
- Use Tailwind CSS v3 and shadcn/ui for styling. Configure dark mode as the default (class strategy).
- Use Zustand for global state management.
- Use React Router v6 for tab-based navigation inside the renderer.
- Set up electron-builder for packaging: NSIS installer for Windows, DMG for macOS.
- Set up electron-updater for auto-update support (GitHub Releases provider).
- Create the full directory structure as follows. Create placeholder index files for each directory so the tree is real but empty stubs compile without errors:

electron/
  main.ts
  preload.ts
  audio-engine/
    dsd-bridge.ts  ffmpeg-runner.ts  flac-runner.ts
    ape-runner.ts  shn-runner.ts  sector-fix.ts
  checksum/
    md5.ts  sfv.ts  ffp.ts  cfp.ts  st5.ts
  torrent/
    create.ts  inspect.ts  hash.ts
  tagger/
    etree-parser.ts  tag-writer.ts  setlist-matcher.ts
  updater/
    binary-updater.ts  sources.ts  launch-counter.ts
  updater.ts
src/
  App.tsx
  components/layout/  components/queue/  components/waveform/
  components/dsd/  components/encoding/  components/checksums/
  components/torrents/  components/tagger/  components/settings/
  hooks/  store/  ipc/
native/  binaries/win/  binaries/mac/

The main process (electron/main.ts) must:
- Create a BrowserWindow (1280x800, min 960x600).
- Set up a context bridge in preload.ts that exposes window.electronAPI with typed IPC channels.
- Register a system tray icon with a right-click menu: Show, Quit.
- Handle the 'open-file' event on macOS (drag file onto dock icon).
- On Windows, register a protocol handler "hi-res-trader://" for Explorer integration.
- On startup, call the launch counter increment and trigger binary update check if schedule is due.

The renderer App.tsx must render a persistent left sidebar (icons only, collapsible) and a main content area with SEVEN tabs: Queue, Waveform Editor, DSD Editor, Live Show Tagger, Checksums, Torrents, Settings. Each tab renders a placeholder component.

The StatusBar at the bottom must show: currently active file (or "No file loaded"), background task count, and app version from package.json.

Output all files with full content. Include package.json with all required dependencies pinned to stable versions. Include tsconfig for main, preload, and renderer. Include electron-builder.config.js.
```

---

### Phase 1 — IPC Bridge & File Routing

**Claude Code Prompt:**

```
In the hi-res-trader Electron project, implement the IPC bridge and file routing system.

FILE: electron/preload.ts
Expose window.electronAPI with the following typed channels using contextBridge.exposeInMainWorld:
- openFile(filters: FileFilter[]): Promise<string[]>
- openFolder(): Promise<string>
- routeFiles(paths: string[]): Promise<RoutedFile[]>
- getFileInfo(path: string): Promise<AudioFileInfo>
- onProgressUpdate(cb): IpcSubscription
- onFileRouted(cb): IpcSubscription
All types must be defined in a shared src/types/ipc.ts file.

FILE: electron/main.ts (additions)
Handle the IPC channels above. For routeFiles:
1. Read each file's extension and, for DSF/DFF files, read the first 4 bytes to confirm the DSD marker.
2. Return a RoutedFile[] where each entry has: path, extension, detectedFormat, suggestedModule.
3. Files with extensions .dsf or .dff → detectedFormat: 'dsd', suggestedModule: 'dsd-editor'.
4. Files with .wav, .flac, .ape, .shn, .mkw → suggestedModule: 'waveform-editor' if file is > 10 minutes long (read duration via ffprobe), else 'encoder'.
5. Batch .md5, .ffp, .sfv, .cfp, .st5 files → suggestedModule: 'checksums'.
6. .torrent files → suggestedModule: 'torrents'.
7. .txt files matching eTree format (first line starts with "Artist:" or "Date:") → suggestedModule: 'tagger'.

FILE: src/ipc/bridge.ts
Wrap window.electronAPI with React-friendly async hooks. Export:
- useOpenFile(filters), useRouteFiles(), useFileInfo(path)
Each hook returns { data, loading, error }.

FILE: src/components/queue/DropZone.tsx
A full-screen drag-and-drop zone that:
- Accepts any file type.
- On drop, calls routeFiles with the dropped paths.
- Shows a routing result dialog: file list with detected format and suggested module, "Route All" button and individual "Change Module" overrides.
- After routing confirmation, dispatches files to the Zustand store under their respective module queues.

FILE: src/store/appStore.ts
Define Zustand store with slices:
- queues: { dsdEditor: string[], waveformEditor: string[], encoder: string[], checksums: string[], torrents: string[], tagger: string[] }
- activeModule: string
- backgroundTasks: { id: string, label: string, progress: number }[]
- currentFile: string | null
Include actions: addToQueue, removeFromQueue, setActiveModule, addBackgroundTask, updateTaskProgress, completeTask.
```

---

### Phase 2 — Encoding / Decoding Module (TLH Core)

**Claude Code Prompt:**

```
In hi-res-trader, implement the Encoding/Decoding module — the modernized core of Trader's Little Helper.

PART A — Binary runner infrastructure (electron/audio-engine/)

FILE: electron/audio-engine/binary-runner.ts
Create a generic CLI runner that:
- Accepts: binaryName (looked up from binaries/win/ or binaries/mac/ based on process.platform), args string[], optional stdin pipe.
- Spawns the process, streams stdout/stderr line by line.
- Emits progress events over IPC (channel: 'progress-update') with { taskId, percent, currentLine }.
- Resolves with { exitCode, stdout, stderr } on completion.
- Supports cancellation via AbortSignal.
- On Windows, suppresses the console window (windowsHide: true).

FILE: electron/audio-engine/ffmpeg-runner.ts
Wrap binary-runner for ffmpeg. Export:
- getFileInfo(path): Promise<AudioFileInfo> — runs ffprobe -v quiet -print_format json -show_streams -show_format
- convertFile(opts: ConvertOptions): Promise<void> — builds ffmpeg args for format + sample rate + bit depth conversion
- trimFile(opts: TrimOptions): Promise<void> — uses -ss / -to for in/out point export

FILE: electron/audio-engine/flac-runner.ts
Wrap binary-runner for flac CLI. Export:
- encode(inputWav, outputFlac, compressionLevel, tags): Promise<void>
- decode(inputFlac, outputWav): Promise<void>
- test(inputFlac): Promise<TestResult>
- reEncode(inputFlac, outputFlac, compressionLevel): Promise<void>

FILE: electron/audio-engine/ape-runner.ts
Wrap binary-runner for mac (Monkey's Audio). Export:
- encode(inputWav, outputApe, compressionLevel): Promise<void>
- decode(inputApe, outputWav): Promise<void>
- test(inputApe): Promise<TestResult>
- convert(inputApe, outputFlac): Promise<void>

FILE: electron/audio-engine/shn-runner.ts
Wrap binary-runner for shorten. Export:
- encode(inputWav, outputShn): Promise<void>
- decode(inputShn, outputWav): Promise<void>
- test(inputShn): Promise<TestResult>
- createSkt(inputShn, outputSkt): Promise<void>

FILE: electron/audio-engine/sector-fix.ts
Pure Node.js implementation. Export:
- analyzeSectorBoundaries(path): Promise<SectorReport>
- fixSectorBoundaries(inputPath, outputPath): Promise<FixResult>
- removeExtraRiffChunks(inputPath, outputPath): Promise<void>
- rewriteWaveHeader(inputPath, outputPath): Promise<void>
- detectMp3Source(wavPath): Promise<Mp3SourceResult>

PART B — Renderer UI (src/components/encoding/)

FILE: src/components/encoding/EncoderPanel.tsx
Panel with file list, output format selector (FLAC compression 0-8, APE levels, SHN, MP3 bitrate), output folder picker, encode options, "Encode All" button, per-file progress bars, status column.

FILE: src/components/encoding/DecoderPanel.tsx
Same structure for decoding to WAV. Includes "Fix after decode" option running sector-fix and RIFF-rewrite automatically.

FILE: src/components/encoding/ToolsPanel.tsx
Utilities panel with collapsible sections: Test Files, Fix Sector Boundaries, Remove Extra RIFF Chunks, Rewrite WAVE Headers, Detect MP3 Source, Create SKT. Each with file picker, action button, result log.
```

---

### Phase 3 — Waveform Editor Module (CD Wave Editor)

**Claude Code Prompt:**

```
In hi-res-trader, implement the Waveform Editor module — a modernized CD Wave Editor using wavesurfer.js.

Install: npm install wavesurfer.js @wavesurfer/regions @dnd-kit/sortable

FILE: electron/audio-engine/splitter.ts
Export:
- splitFile(inputPath, markers: SplitMarker[], outputFolder, outputFormat): Promise<SplitResult[]>
  Uses ffmpeg-runner to slice at each marker pair. SplitMarker: { index, startSec, endSec, trackName, enabled }.
- exportCueSheet(inputPath, markers: SplitMarker[], outputPath): Promise<void>
  Writes a standard .cue file.

FILE: src/components/waveform/WaveformPanel.tsx
Top-level layout: WaveformOverview (top strip), WaveformZoom (main pane), transport controls row, SplitControls row, MarkerList panel (right sidebar), bottom action bar (output folder, format, Export Tracks).

FILE: src/components/waveform/WaveformOverview.tsx
wavesurfer.js in read-only overview mode (80px height). Draws colored bands between split markers. Clicking seeks both panes. Zoom viewport shown as highlighted region.

FILE: src/components/waveform/WaveformZoom.tsx
Main wavesurfer.js instance: scrollable, zoomable (mousewheel), Regions plugin for draggable/resizable colored regions, right-click context menu (Insert split here, Remove nearest split), auto-scrolls during playback. Regions sync bidirectionally with MarkerList.

FILE: src/components/waveform/MarkerList.tsx
Scrollable list: #, Track Name (inline editable), Start (MM:SS.mmm editable), Duration, enabled checkbox. Click row → seek. Delete removes marker. Drag-to-reorder via @dnd-kit/sortable. "Add Split at Current Position" button. Track names default to "Track 01", "Track 02", etc.

FILE: src/components/waveform/SplitControls.tsx
Play/Pause/Stop, current position clock (HH:MM:SS.mmm, updates 100ms), "Split Here" button (inserts marker at current playback position), zoom level display, "Snap to nearest zero crossing" toggle.

FILE: src/hooks/useWaveform.ts
Custom hook managing shared wavesurfer.js instance. Exposes: currentTime, duration, isPlaying, zoom, markers[], and methods: play, pause, stop, seek, addMarker, removeMarker, updateMarker, setZoom. Syncs marker state to Zustand. For APE/SHN: decode to WAV via electron IPC before loading.
```

---

### Phase 4 — DSD Editor Module (TASCAM Hi-Res Editor)

**Claude Code Prompt:**

```
In hi-res-trader, implement the DSD Editor module. Use ffmpeg for all DSD conversion (see DSD note in architecture doc). Do NOT require xrecode or any paid CLI.

FILE: electron/audio-engine/dsd-runner.ts
Export:
- getDsdInfo(path): Promise<DsdFileInfo>
  Reads DSF/DSDIFF header via pure Node.js Buffer parsing.
  DSF: bytes 0-3 = 'DSD ', parse file size, sample rate, channel count, bit depth from block headers per Sony DSF spec.
  DSDIFF: bytes 0-3 = 'FRM8', bytes 12-15 = 'DSD ', parse PROP chunk for sample rate/channels.
  Returns: { format, sampleRate, bitDepth, channels, durationSec, fileSize }

- convertDsdToPreviewWav(dsdPath, outputWavPath, targetSampleRate): Promise<void>
  ffmpeg -i input.dsf -af aresample=resampler=soxr -ar {targetSampleRate} -c:a pcm_s24le output.wav
  Valid targetSampleRate values: 88200, 176400, 352800.

- exportDsd(opts: DsdExportOptions): Promise<void>
  opts: { inputPath, outputPath, outputFormat: 'DSF'|'DSDIFF'|'WAV', inPointSec, outPointSec, targetSampleRate, bitDepth, applyShortFade }
  DSD→WAV: ffmpeg with -ss / -to for trim, aresample for conversion, optional afade for short fade.
  DSD→DSD trim: ffmpeg -i input.dsf -ss {inPoint} -to {outPoint} -c copy output.dsf
  If applyShortFade: append -af afade=t=out:st={outPointSec - 0.02}:d=0.02

- combineDsd(headPath, tailPath, outputPath): Promise<void>
  Validates matching sample rate, bit depth, channel count.
  Uses ffmpeg: ffmpeg -i head.dsf -i tail.dsf -filter_complex "[0:a][1:a]concat=n=2:v=0:a=1" output.wav
  Then, if output is DSF/DSDIFF, re-encode via ffmpeg's DSD encoder.

FILE: src/components/dsd/DsdPlayer.tsx
Main DSD editor view matching TASCAM Hi-Res Editor layout:
- Top bar: Audio Driver selector, Audio Device selector, Play Frequency selector, Control Panel button.
- Stereo level meters (dB scale: -48 to +3 OVER), updating at 30fps via Web Audio AnalyserNode.
- Large monospace time display (HH:MM:SS.mmm, dark bg, green text).
- DsdWaveform component.
- In Point / Out Point / In-Out duration readouts.
- SET POINT row: IN and OUT buttons.
- Transport: play-to-in, Stop, Play.
- Action buttons: OPEN, EXPORT, COMBINE.
- File info bar: filename, duration, format, sample rate, bit depth, channel count.

FILE: src/components/dsd/DsdWaveform.tsx
On file open: triggers convertDsdToPreviewWav via IPC (shows progress spinner). Loads preview WAV into wavesurfer.js. Two blue circular draggable handles: In Point (left), Out Point (right). Yellow vertical playhead. Zoom +/- buttons on right.

FILE: src/components/dsd/InOutControls.tsx
In Point display (editable MM:SS.mmm), Out Point display (editable), In-Out duration (read-only). SET IN / SET OUT buttons. "Play from In" button. "Loop In-Out" toggle.

FILE: src/components/dsd/ExportDialog.tsx
Modal matching TASCAM Hi-Res Editor Export dialog: Original File info (read-only), Export To section with Folder, File Name, File Format (WAV/DSF/DSDIFF), Quantization Bit, Frequency, Channel (read-only), Apply Short Fade checkbox, EXPORT button with progress bar.

FILE: src/components/dsd/CombineDialog.tsx
Modal matching TASCAM Combine Files dialog: Head File (Select button, format info), Tail File (Select button, format info), Combined File (folder + name), format compatibility warning badge, COMBINE button (disabled if incompatible).

FILE: src/hooks/useDsdPlayer.ts
State: { file, previewWavPath, isConverting, conversionProgress, inPoint, outPoint, isPlaying, currentTime }
Methods: openFile, setInPoint, setOutPoint, play, pause, stop, seekToInPoint, export, combine
```

---

### Phase 5 — Checksum Module

**Claude Code Prompt:**

```
In hi-res-trader, implement the Checksum module (MD5, SFV, FFP, CFP, ST5).

FILE: electron/checksum/md5.ts
- computeMd5(filePath): Promise<string> — streams file through Node crypto createHash('md5')
- createMd5File(filePaths: string[], outputPath: string): Promise<void> — BSD-style: <hash>  <filename>\n
- verifyMd5File(md5FilePath: string): Promise<ChecksumVerifyResult[]>

FILE: electron/checksum/sfv.ts
- computeCrc32(filePath): Promise<string> — CRC32 via crc npm package
- createSfvFile(filePaths: string[], outputPath: string): Promise<void>
- verifySfvFile(sfvFilePath: string): Promise<ChecksumVerifyResult[]>

FILE: electron/checksum/ffp.ts
- computeFlacFingerprint(flacPath): Promise<string> — runs flac --test, parses "MD5 signature: <hash>" from stderr
- createFfpFile(flacPaths: string[], outputPath: string): Promise<void> — format: <filename>:<md5>
- verifyFfpFile(ffpFilePath: string): Promise<ChecksumVerifyResult[]>

FILE: electron/checksum/cfp.ts
JSON format: { "files": [ { "name", "size", "md5", "crc32" } ] }
- createCfpFile(filePaths: string[], outputPath: string): Promise<void>
- verifyCfpFile(cfpFilePath: string): Promise<ChecksumVerifyResult[]>

FILE: electron/checksum/st5.ts
Identical structure to SFV (CRC32, .st5 extension, associated with shntool).
- createSt5File / verifySt5File (reuse sfv.ts CRC32 logic)

FILE: src/components/checksums/ChecksumPanel.tsx
Two-tab panel: Verify and Create.
Verify: drop zone for checksum files, source folder override, Verify button, results table (filename / expected / computed / PASS✓/FAIL✗/MISSING), summary bar, Copy and Export buttons.
Create: file list, checksum type multi-select (MD5/SFV/FFP/CFP/ST5), output folder, Create button, progress log.
All IPC calls report per-file progress.
```

---

### Phase 6 — Torrent Module

**Claude Code Prompt:**

```
In hi-res-trader, implement the Torrent module.

Install: npm install create-torrent parse-torrent (main process only)

FILE: electron/torrent/create.ts
- createTorrent(opts): Promise<void>
  opts: { inputFolder, outputTorrentPath, announceUrls, comment, pieceLength, isPrivate, createdBy }
  Default trackers: http://bt.etree.org:6969/announce, http://tracker.bt4g.com:2095/announce
  Reports per-piece hashing progress over IPC.

FILE: electron/torrent/inspect.ts
- inspectTorrent(torrentPath): Promise<TorrentInfo>
  Returns: { name, infoHash, announce, comment, createdBy, creationDate, pieceLength, pieces, totalSize, files[] }

FILE: electron/torrent/hash.ts
- hashTorrentAgainstFiles(torrentPath, localFolder): Promise<TorrentHashResult>
  Verifies file existence, size, and SHA1 piece hashes against torrent metadata.
  Returns: { totalPieces, matchedPieces, failedPieces, missingFiles, corruptFiles, result: 'OK'|'PARTIAL'|'FAIL' }
  Reports per-piece progress over IPC.

FILE: src/components/torrents/TorrentCreate.tsx
Input folder, tracker URL list (editable, add/remove, pre-populated with defaults), comment, piece size selector (Auto/256KB/512KB/1MB/2MB/4MB), private checkbox, output path, Create button with progress bar, info hash display on completion.

FILE: src/components/torrents/TorrentInspect.tsx
.torrent drop zone. Displays: name, info hash, total size, creation date, trackers, comment, piece info, file tree table (path / size / offset). "Copy info hash" button.

FILE: src/components/torrents/TorrentHash.tsx
Torrent picker, local folder picker, Verify button, piece-by-piece progress bar, overall status badge (OK/PARTIAL/FAIL), missing/corrupt file lists, "Copy result report" button.
```

---

### Phase 7 — Live Show Tagger Module

**Goal:** Implement the eTree/furthur-style metadata tagger and text file generator — a full replacement for foo_tradersfriend / Live Show Tagger that is built directly into the app, with no foobar2000 dependency.

**Background:** The eTree community uses plain-text "info files" to describe live recordings. These files are the canonical metadata source for audio file tagging. The Live Show Tagger (foo_tradersfriend) plugin for foobar2000 is the most-used tool for this workflow but has not been updated since 2010 and runs only on Windows. Hi-Res Trader replaces it entirely.

**Claude Code Prompt:**

```
In hi-res-trader, implement the Live Show Tagger module — a built-in replacement for foo_tradersfriend / Live Show Tagger.

PART A — Backend (electron/tagger/)

FILE: electron/tagger/etree-parser.ts
Export a pure TypeScript eTree text file parser and generator.

PARSER: parseEtreeFile(content: string): EtreeShow
EtreeShow type:
{
  artist: string,
  date: string,           // YYYY-MM-DD
  venue: string,
  location: string,       // "City, State" or "City, Country"
  source: string,         // e.g., "SBD > DAT > CDR > EAC > FLAC"
  notes: string,          // any free-form text not matching structured fields
  sets: EtreeSet[]
}
EtreeSet type: { label: string, tracks: EtreeTrack[] }
EtreeTrack type: { globalIndex: number, setIndex: number, title: string, comment: string }

Parsing rules:
- Lines starting with "Artist:", "Date:", "Venue:", "Location:", "Source:" are key-value fields (case-insensitive key).
- A line matching /^(Set \d+|Encore\d*|E\d*):?$/i starts a new set. Capture the label.
- A line matching /^\d+[\.\)]\s+(.+)$/ is a track entry. Parse the number as globalIndex and the rest as title.
  Track comments: if title contains " > " or text in parentheses after the title, capture as comment.
- Blank lines are ignored. Any other lines append to notes.
- The output sets array must be ordered as they appear in the file.
- Track globalIndex is the absolute track number across all sets (for tagging TRACKNUMBER).
- Track setIndex is the position within its set (for display).

GENERATOR: generateEtreeFile(show: EtreeShow): string
Produce the canonical text format:
  Artist: {artist}
  Date: {date}
  Venue: {venue}
  Location: {location}
  Source: {source}
  
  {notes if present, followed by blank line}
  
  Set 1:
  1. Track Title One
  2. Track Title Two
  
  Set 2:
  3. Track Title Three
  4. Track Title Four
  
  Encore:
  5. Encore Track
  
Rules:
- One blank line between the header block and first set.
- One blank line between sets.
- Track numbers are global (continuous across sets).
- If only one set exists and it has no label (label is empty or "Set 1"), still output "Set 1:".
- Encore label appears as "Encore:" regardless of how many encores.
- Trailing newline at end of file.

FILE: electron/tagger/setlist-matcher.ts
Export:
- matchFilesToSetlist(audioPaths: string[], show: EtreeShow): MatchResult[]
  Matches audio files to setlist tracks by sort order (lexicographic filename sort).
  Returns MatchResult[]: { filePath, track: EtreeTrack | null, matchStatus: 'matched'|'unmatched'|'extra' }
  Files beyond the track count are marked 'extra'. Tracks without files are unmatched.
  Also returns a summary: { matchedCount, unmatchedCount, extraCount, totalTracks, totalFiles }

FILE: electron/tagger/tag-writer.ts
Export:
- writeTagsToFile(filePath: string, tags: AudioTags): Promise<void>
  AudioTags: { artist, albumArtist, album, date, venue, location, source, trackNumber, trackTotal, title, comment, setNumber }

  Tag mapping by format:
  FLAC (.flac): Use metaflac CLI — metaflac --remove-all-tags --set-tag="ARTIST=..." etc.
    Map: ARTIST, ALBUMARTIST, ALBUM (= "Artist - Date"), DATE, VENUE, LOCATION, SOURCE,
         TRACKNUMBER (= "01/15" format), TITLE, COMMENT, DISCNUMBER (= set number).

  MP3 (.mp3): Use node-id3 npm package (pure JS, no binary needed).
    Map ID3v2.3 tags: TPE1 (artist), TPE2 (albumArtist), TALB (album), TDRC (date),
         TRCK (track/total), TIT2 (title), COMM (comment), TPOS (disc/set).
    Custom TXXX frames for VENUE, LOCATION, SOURCE.

  APE (.ape, .aiff): Use ffmpeg to copy stream and inject metadata:
         ffmpeg -i input.ape -c copy -metadata ARTIST="..." output.ape
    This rewrites the APEv2 tag block.

  WAV (.wav): Use ffmpeg metadata injection same as APE above; writes INFO chunk tags.

  DSDIFF/DSF (.dff, .dsf): Tag support is limited. Use ffmpeg to copy and inject ID3 block (DSF supports embedded ID3).

- readTagsFromFile(filePath: string): Promise<AudioTags>
  Reads existing tags using ffprobe (ffprobe -v quiet -print_format json -show_format returns "tags" object).
  Returns AudioTags with all readable fields populated.

PART B — Renderer UI (src/components/tagger/)

FILE: src/components/tagger/TaggerPanel.tsx
Main layout — two-column design matching Live Show Tagger's aesthetic but modernized:

LEFT COLUMN — "Tracks" panel:
- Header: "Tracks" label, folder open button (to load a folder of audio files), file count.
- File list (TrackList component).
- Status bar: "N tracks matched", "N unmatched", "N extra files".

RIGHT COLUMN — "Data" panel:
- "Text File" section at top: dropdown showing loaded .txt filename (or "[unresolved]"), Load button, Unload button, Reload button, Edit button (opens SetlistEditor in a modal).
- MetaDataFields component: Artist, Date, Venue, Location, Source fields, each with a "Revert" button (reverts to value from loaded text file).
- SetlistEditor preview: inline read-only table showing #, Title, Comment for current setlist. "Edit Setlist" button opens the full editor.
- "Generate Text File" button — prominent, at bottom of Data column.
- "Update Files" button — applies tags to all matched files.

FILE: src/components/tagger/TrackList.tsx
Scrollable file list:
- Columns: Filename (truncated), Track # (e.g., "1/15"), Match status indicator (green dot = matched, yellow = extra, red = unmatched).
- Clicking a row selects it and highlights the corresponding setlist entry.
- Supports multi-select for manual match override.
- Sort: always lexicographic by filename (same order as setlist matching).

FILE: src/components/tagger/MetaDataFields.tsx
Editable fields: Artist, Date, Venue, Location, Source.
- Each field: label, text input, Revert button.
- Revert restores the value from the loaded EtreeShow object.
- Date field: enforces YYYY-MM-DD format with a simple inline mask.
- Source field: multi-line text area (source chains can be long, e.g., "SBD > Nakamichi 550 > Tascam DA-30 > SBX Pro > Samplitude v8 > CDWave > FLAC").
- All field values are kept in Zustand tagger slice and are what gets written to both tags and the generated text file.

FILE: src/components/tagger/SetlistEditor.tsx
Full setlist editor (modal dialog):
- "Add Set" button — appends a new set (Set 1, Set 2, Encore, etc., auto-labeled).
- Per-set section:
  - Set label (editable: "Set 1", "Set 2", "Encore", etc.).
  - Track list within set: #, Title (editable), Comment (editable).
  - "Add Track" button appends a track. Drag to reorder within set (via @dnd-kit/sortable).
  - Trash icon removes a track.
- "Move to Set" dropdown per track: move a track to a different set.
- Global track numbers auto-update as tracks are added/removed/moved.
- "Auto-Number from Filenames" button: attempts to extract track numbers from leading digits in filenames (e.g., "01 - Song.flac" → track 1).
- "OK" and "Cancel" buttons. OK commits changes to Zustand store.

FILE: src/components/tagger/TextFileGenerator.tsx
Not a standalone component — a function called by the "Generate Text File" button in TaggerPanel.
Logic:
1. Get current EtreeShow state from Zustand.
2. Call generateEtreeFile(show) to produce the text content.
3. Determine output filename: take the parent folder name of the loaded audio files. Output file = {parentFolderName}.txt, saved in that same folder.
   Example: if files are in "C:/Shows/Phish1997-12-31", output is "C:/Shows/Phish1997-12-31/Phish1997-12-31.txt".
4. Write the file via IPC (new channel: writeTextFile(path, content): Promise<void>).
5. Show a success toast: "Generated Phish1997-12-31.txt" with an "Open" link.

FILE: src/hooks/useTagger.ts
Custom hook managing tagger state:
- State: { audioFiles: string[], show: EtreeShow | null, matchResults: MatchResult[], isDirty, isTagging, taggingProgress }
- Methods:
  - loadFolder(folderPath): scans for audio files, sorts lexicographically, triggers matching.
  - loadTextFile(txtPath): calls parseEtreeFile, stores result, triggers matching.
  - unloadTextFile(): clears show state.
  - reloadTextFile(): re-reads the file from disk.
  - updateShowField(field, value): updates a single show field (marks isDirty = true).
  - updateSetlist(sets): replaces setlist.
  - tagAllFiles(): iterates matchResults, calls writeTagsToFile for each matched file.
  - generateTextFile(): calls generateEtreeFile and writeTextFile.
  - rematch(): re-runs setlist-matcher when files or show change.

IPC channels to add in electron/main.ts:
- writeTextFile(path, content): Promise<void> — writes UTF-8 text to the given path.
- readTextFile(path): Promise<string> — reads a text file.
- scanFolder(folderPath, extensions): Promise<string[]> — returns sorted list of audio files in folder matching given extensions.
```

---

### Phase 8 — Binary Tool Auto-Updater

**Goal:** Keep bundled CLI tools current automatically, using a 7-day OR 10-launch schedule — the industry standard used by VS Code, Spotify, and Chrome. Users are notified of updates but never interrupted mid-task.

**Update source registry:**

| Tool | Platform | Source | Method |
|---|---|---|---|
| ffmpeg + ffprobe | Windows | github.com/BtbN/FFmpeg-Builds | GitHub Releases API (latest release, asset matching `ffmpeg-master-latest-win64-gpl.zip`) |
| ffmpeg + ffprobe | macOS | evermeet.cx/ffmpeg | JSON API at `https://evermeet.cx/ffmpeg/info/ffmpeg/release` and `https://evermeet.cx/ffprobe/info/ffprobe/release` |
| flac | Both | github.com/xiph/flac | GitHub Releases API (latest release, asset matching platform) |
| mac (Monkey's Audio) | Windows | monkeysaudio.com | HTML scrape of download page (no API — fall back to 90-day check interval, very infrequent releases) |
| mac substitute | macOS | ffmpeg only | No mac CLI on macOS; APE decode via ffmpeg's built-in APE decoder. Skip mac updates on Mac. |
| shorten | Both | Bundle fixed version 3.6.1 | No updates expected; mark as "vendor-pinned" in update registry. Do not check. |

**Claude Code Prompt:**

```
In hi-res-trader, implement the Binary Tool Auto-Updater.

FILE: electron/updater/launch-counter.ts
Uses electron-store to persist:
- launchCount: number (increments on every app start)
- lastUpdateCheck: ISO timestamp string (updated after each check run)

Export:
- incrementLaunch(): void — called on app start before window is shown.
- shouldCheckForUpdates(): boolean
  Returns true if EITHER:
  (a) It has been >= 7 days since lastUpdateCheck, OR
  (b) launchCount is a multiple of 10 (i.e., launchCount % 10 === 0)
  This matches the industry-standard dual-trigger pattern (time OR launch threshold).
- recordCheckCompleted(): void — sets lastUpdateCheck to now, does NOT reset launchCount.

FILE: electron/updater/sources.ts
Define a static registry of update sources. Each entry:
{
  toolName: string,          // 'ffmpeg', 'ffprobe', 'flac', 'mac'
  platforms: ('win32'|'darwin')[],
  checkIntervalDays: number, // 7 for GitHub-sourced tools, 90 for scraped/pinned
  vendorPinned: boolean,     // if true, skip update checks entirely
  getLatestVersion: () => Promise<BinaryUpdateInfo>,
  getDownloadUrl: (version: string) => Promise<string>
}

BinaryUpdateInfo: { version: string, releaseDate: string, downloadUrl: string, sha256: string | null }

Implement getLatestVersion for each tool:

ffmpeg (Windows):
  GET https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest
  Parse tag_name for version. Find asset named "ffmpeg-master-latest-win64-gpl.zip".
  Return downloadUrl from asset browser_download_url.

ffmpeg (macOS):
  GET https://evermeet.cx/ffmpeg/info/ffmpeg/release (returns JSON with version, download, sha256)
  GET https://evermeet.cx/ffprobe/info/ffprobe/release (same for ffprobe)

flac (both platforms):
  GET https://api.github.com/repos/xiph/flac/releases/latest
  Parse tag_name for version. Find asset matching platform:
  Windows: name contains "win" and ends in ".zip"
  macOS: name contains "macos" or "osx" and ends in ".zip" or ".tar.xz"

mac / Monkey's Audio (Windows only):
  GET https://www.monkeysaudio.com/download.html
  Parse the HTML to find the current version number and download link.
  Use a simple regex: /MAC_(\d+\.\d+)_installer\.exe/ to extract version.
  If parsing fails, log a warning and return null (skip this update cycle).

shorten: vendorPinned: true — skip entirely.

FILE: electron/updater/binary-updater.ts
Export:
- checkAllTools(): Promise<UpdateCheckSummary>
  For each tool in sources registry (filtered by current platform, skipping vendorPinned):
  1. Read the currently bundled binary version from a versions.json file stored in the binaries/ directory.
  2. Call getLatestVersion() for the tool.
  3. Compare versions. If latest > current, add to pendingUpdates list.
  4. Handle network errors gracefully: log, skip that tool, continue.
  Returns: { checkedAt, pendingUpdates: BinaryUpdate[], errors: string[] }

- downloadAndStageUpdate(tool: BinaryUpdate): Promise<void>
  1. Download the archive from downloadUrl to a temp directory (app.getPath('temp')).
  2. Verify SHA256 if provided in BinaryUpdateInfo (use Node crypto).
  3. Extract the specific binary file(s) from the archive (zip: use adm-zip npm package, tar.xz: use tar npm package).
  4. Copy extracted binary to binaries/{platform}/staging/{toolName}[.exe].
  5. Update staging/versions.json with the new version.
  Emit IPC progress events during download (progress-update channel).

- applyAllStagedUpdates(): Promise<ApplyResult>
  Called at app start, before the main window is shown, if any staged updates exist.
  For each staged binary:
  1. Replace the active binary in binaries/{platform}/ with the staged version.
  2. On Windows: if the binary is currently locked (in use), schedule replacement for next restart via a pending-updates flag.
  3. Update the active versions.json.
  4. Log the update in the update history (stored in electron-store).
  Returns: { applied: string[], deferred: string[], errors: string[] }

- getUpdateHistory(): Promise<UpdateHistoryEntry[]>
  Returns the log of past updates: { toolName, oldVersion, newVersion, appliedAt }

Add IPC channels in electron/main.ts:
- checkForBinaryUpdates(): Promise<UpdateCheckSummary>
- getBinaryVersions(): Promise<Record<string, string>>   — returns { ffmpeg: '7.x', flac: '1.4.x', ... }
- getUpdateHistory(): Promise<UpdateHistoryEntry[]>
- applyPendingUpdates(): Promise<ApplyResult>

FILE: src/components/settings/ToolVersionsPanel.tsx (sub-panel within SettingsPanel)
"Bundled Tools" section in Settings:
- Table: Tool, Current Version, Latest Known, Last Checked, Status (Up to date ✓ / Update available ↑ / Check failed ⚠).
- "Check Now" button: calls checkForBinaryUpdates, refreshes table.
- "Download Updates" button (visible only when updates are available): calls downloadAndStageUpdate for each pending update, shows per-tool download progress bars.
- "Apply Updates" button (visible only when staged updates exist): calls applyPendingUpdates. Shows result. If any were deferred (file locked), shows "Restart to apply remaining updates."
- Update history accordion at bottom: shows last 20 update events.
- Update schedule note: "Hi-Res Trader checks for tool updates every 7 days or every 10 launches, whichever comes first."
```

---

### Phase 9 — Settings, Auto-Update & Windows Explorer Integration

**Claude Code Prompt:**

```
In hi-res-trader, implement Settings, app auto-update, and Windows Explorer integration.

FILE: electron/main.ts (additions)
Auto-update:
- Import electron-updater autoUpdater.
- On app ready: call autoUpdater.checkForUpdatesAndNotify() if not in dev mode.
- Handle events: update-available (tray notification), update-downloaded (dialog: "Restart to apply update?" with Restart / Later).
- Expose IPC: checkForUpdate, getVersion.

Windows Explorer context menu (Windows only):
- On first launch, prompt user to register context menu entries.
- Write registry entries via bundled PowerShell script (scripts/register-context-menu.ps1) under HKCU\Software\Classes\*\shell\HiResTrader:
  - "Open with Hi-Res Trader" → launch app with file path as argv[1].
  - Sub-entries: "Add to Queue", "Encode to FLAC", "Verify Checksums", "Tag with Live Show Tagger".
- Provide "Unregister" option in Settings.

FILE: electron/settings.ts
Persistent typed settings via electron-store:
{
  general: { theme, language, checkUpdatesOnStart },
  paths: { defaultOutputFolder, ffmpegPath, flacPath, macPath, shortenPath },
  encoding: { defaultFlacLevel, defaultApeLevel, defaultMp3Bitrate },
  dsd: { defaultExportFormat, defaultSampleRate, defaultBitDepth, audioDriver, audioDevice },
  waveform: { defaultSplitOutputFormat, snapToZeroCrossing },
  checksums: { defaultTypes: string[] },
  torrents: { defaultTrackers, defaultPieceSize, isPrivate, createdBy },
  tagger: { defaultArtist, autoTagOnLoad, autoGenerateTextFile },
  binaryUpdater: { checkIntervalDays: 7, launchInterval: 10, lastCheck, autoDownload: false },
  explorer: { contextMenuRegistered }
}
Expose IPC: getSettings, updateSettings, resetSettings.

FILE: src/components/settings/SettingsPanel.tsx
Tabs: General, Paths, Encoding, DSD, Waveform, Checksums, Torrents, Tagger, Bundled Tools, About.

Tagger tab:
- Default Artist field (pre-fills Artist when a new folder is loaded with no text file).
- "Auto-tag files when text file loads" toggle.
- "Auto-generate text file after tagging" toggle.

Bundled Tools tab: ToolVersionsPanel component (from Phase 8).

About tab: version, project page link, issue tracker link, Check for App Updates button, Register/Unregister Windows Explorer context menu buttons (Windows only).
```

---

### Phase 10 — Polish, Packaging & Binary Bundling

**Claude Code Prompt:**

```
In hi-res-trader, finalize bundling, binary inclusion, versions manifest, and UX polish.

PART A — Binary bundling and versions manifest

Create binaries/versions.json:
{
  "ffmpeg": { "version": "7.1", "platform": "win32", "bundledAt": "2025-01-01" },
  "ffprobe": { "version": "7.1", "platform": "win32", "bundledAt": "2025-01-01" },
  "flac": { "version": "1.4.3", "platform": "win32", "bundledAt": "2025-01-01" },
  "mac": { "version": "10.38", "platform": "win32", "bundledAt": "2025-01-01" },
  "shorten": { "version": "3.6.1", "platform": "win32", "bundledAt": "2025-01-01", "vendorPinned": true }
}
Create the same structure for mac platform.

Download and vendor the following prebuilt CLI binaries into binaries/win/ and binaries/mac/:
- ffmpeg / ffprobe: BtbN/FFmpeg-Builds (Windows static), evermeet.cx (Mac static)
- flac: xiph.org/flac official releases
- mac (Monkey's Audio): monkeysaudio.com (Windows only; macOS uses ffmpeg APE decoder)
- shorten: etree.org/shnutils/shorten/ v3.6.1 (compile from source; include prebuilt in repo)
- sacd_extract: github.com/sacd-ripper/sacd-ripper (optional; include if SACD ISO support is desired)

In electron-builder.config.js:
- Include binaries/ as extraResources.
- Resolve binary paths from process.resourcesPath (production) or project root (dev).
- macOS: chmod +x all binaries in afterPack hook.
- macOS: add binaries to entitlements for hardened runtime.

PART B — Global UX polish

FILE: src/components/layout/Sidebar.tsx
Icon sidebar with icons for: Queue, Waveform Editor, DSD Editor, Live Show Tagger, Checksums, Torrents, Settings.
Active module: accent color highlight.
Drag-and-drop target: files dropped onto sidebar icon route directly to that module.
Tagger icon: badge showing unmatched track count (if > 0).

FILE: src/components/layout/StatusBar.tsx
Left: current file name + format badge.
Center: background task count with spinner; click → popover with task list and individual progress bars.
Right: app version + update badge (app update or tool update available).

FILE: src/App.tsx (global drag-and-drop)
Register dragover and drop on root. On drop anywhere: call routeFiles, show routing dialog. Prevent default browser behavior.

FILE: src/components/queue/FileQueue.tsx
Master queue: filename, format, size, duration, module (color-coded badge), status, actions. "Process All" button. Flat/grouped toggle.

PART C — Testing scaffold

Create tests/ with:
- tests/etree-parser.test.ts: Unit tests for parseEtreeFile and generateEtreeFile.
  Test cases: single set, multi-set with encore, missing fields, extra whitespace, tracks with comments, round-trip (parse then generate produces identical output).
- tests/setlist-matcher.test.ts: Tests for matchFilesToSetlist with files < tracks, files > tracks, exact match.
- tests/sector-fix.test.ts: Tests for sector boundary analysis using synthetic WAV buffers.
- tests/checksum.test.ts: Tests for MD5, CRC32, FFP computation against known values.
- tests/binary-updater.test.ts: Mock GitHub API responses; test shouldCheckForUpdates, version comparison logic.
- tests/routing.test.ts: Tests for routeFiles across all supported extensions including .txt eTree detection.

Use Vitest. Configure vitest.config.ts for Node environment (main-process code), no browser globals.
```

---

## Updated Milestone Table

| Milestone | Phases | Deliverable |
|---|---|---|
| M0 — Shell | Phase 0 | Electron app launches, 7 tabs render, drag-drop routes files |
| M1 — Encode/Decode | Phases 0–2 | Full TLH-equivalent encoding pipeline working |
| M2 — Waveform Split | Phase 3 | CD Wave-equivalent track splitting working |
| M3 — DSD Edit | Phase 4 | TASCAM-equivalent DSD open/preview/trim/export (via ffmpeg, free) |
| M4 — Checksums | Phase 5 | Full checksum create/verify for all 5 types |
| M5 — Torrents | Phase 6 | Create, inspect, hash-verify torrents |
| M6 — Live Show Tagger | Phase 7 | eTree parse/tag/generate text file, full setlist editor |
| M7 — Binary Updater | Phase 8 | 7-day/10-launch auto-updater for bundled tools with staging/apply |
| M8 — Ship | Phases 9–10 | Signed installers, context menu, app auto-update, tests passing |

---

## Key Technical Risks & Mitigations

**DSD native playback** is the hardest feature. ASIO requires a C++ native addon and the Steinberg ASIO SDK (free to use but not open-source redistributable). For v1, route all playback through ffmpeg DSD→PCM conversion — this is fully functional and what most software does. Xrecode CLI is paid ($10) and cannot be bundled in a free app; ffmpeg covers all the same conversion paths at no cost. Advertise native ASIO as a v2 feature.

**APE on macOS**: The mac (Monkey's Audio) CLI is Windows-only. On macOS, use ffmpeg's built-in APE decoder for all decode operations. For APE encoding on Mac, use ffmpeg's APE encoder (it exists but is rarely used on Mac; APE is a Windows-centric format). A settings warning on Mac can note: "APE encoding quality may differ from the Windows mac CLI."

**SHN**: The shorten binary is old (last updated 2004) and requires compilation from source. For v1, use ffmpeg's shorten decoder for decode-only support on macOS. Provide the shorten binary for encode on Windows. macOS users rarely need SHN encode.

**FFP computation**: Run `flac --test` and parse the MD5 from its output — this uses FLAC's own verified implementation and avoids reimplementing the FLAC audio MD5 algorithm in Node.js.

**Sector-fix for FLAC/APE/SHN**: These must be decoded to WAV first, fixed, then re-encoded. Always work on copies and verify checksums before and after.

**Binary auto-updater on macOS**: macOS Gatekeeper may quarantine downloaded binaries. After staging, apply `xattr -d com.apple.quarantine` via a child process to clear the quarantine bit before moving binaries into place. Document this in the afterPack hook for electron-builder.

**eTree text file encoding**: Real-world eTree files use inconsistent line endings (CR/LF vs LF) and sometimes Windows-1252 encoding. The parser must normalize line endings and attempt UTF-8 decode, falling back to latin1/windows-1252 if UTF-8 fails. Use the `chardet` npm package for encoding detection.

**Monkey's Audio website scraping** for update checks is fragile. If the page structure changes, the update check will silently fail (log the error, do not crash). Consider pinning mac to a 90-day update interval since releases are extremely infrequent.
