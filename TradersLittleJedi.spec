# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Trader's Little Jedi
# Build on Mac:     bash build_mac.sh
# Build on Windows: powershell -File build_windows.ps1

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

ROOT = Path(SPECPATH)

# internetarchive (LMA upload) is imported lazily and has submodules + data
# files (its metadata schemas). Collect them so the bundled app can upload.
# Returns [] cleanly if the package isn't installed in the build venv.
_IA_HIDDEN = collect_submodules("internetarchive")
_IA_DATA = collect_data_files("internetarchive")

# Pillow + tkinterdnd2 are bundled on Windows (drag-drop works there) but
# excluded on macOS so the universal2 build doesn't choke on their arm64-only
# native code. Both are optional at runtime.
_OPT_HIDDEN = [] if IS_MAC else ["PIL", "PIL.Image", "PIL.ImageTk", "tkinterdnd2"]
_MAC_EXCLUDE = ["PIL", "tkinterdnd2"] if IS_MAC else []

# Universal2 (Intel + Apple Silicon) only when TLJ_UNIVERSAL is set — CI sets it
# on a universal2 Python runner. A plain local build (single-arch Python) leaves
# this None so it builds for the host arch and doesn't error.
_MAC_ARCH = "universal2" if (IS_MAC and os.environ.get("TLJ_UNIVERSAL")) else None

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "TradersLittleJedi.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "audiohelper"),   "audiohelper"),
        (str(ROOT / "trackers.txt"),  "."),
        # Windows: bundle the CLI tools folder
        # (str(ROOT / "tools"), "tools"),   # uncomment on Windows if tools/ is populated
    ] + _IA_DATA,
    hiddenimports=[
        # tkinter — collected by hook but list explicitly to be safe
        "tkinter", "tkinter.ttk", "tkinter.filedialog",
        "tkinter.messagebox", "tkinter.simpledialog",
        "_tkinter",
        # mutagen
        "mutagen", "mutagen.flac", "mutagen.mp3", "mutagen.easyid3",
        "mutagen.id3", "mutagen.id3._tags", "mutagen.id3._frames",
        "mutagen.mp4", "mutagen.oggvorbis", "mutagen.oggopus",
        "mutagen.wave", "mutagen.aiff",
        # audiohelper submodules imported lazily inside functions
        # (PyInstaller's static scan misses `from .x import Y` inside methods)
        "audiohelper.batch_convert", "audiohelper.bulk_tagger",
        "audiohelper.show_splitter", "audiohelper.jedi_tagger",
        "audiohelper.live_tagger", "audiohelper.tc_tagger",
        "audiohelper.tc_sources", "audiohelper.theme", "audiohelper.lma_upload",
        # stdlib used dynamically
        "array", "json", "threading", "subprocess", "pathlib",
        "tempfile", "shutil", "re", "struct", "zlib", "webbrowser",
    ] + _IA_HIDDEN + _OPT_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclude heavy/optional deps and the hidden HighGrabber subpackage.
    # On macOS we also exclude Pillow (arm64-only C extensions) and
    # tkinterdnd2 (single-arch tkdnd lib) so the universal2 build succeeds —
    # both are optional at runtime (cover preview / drag-drop degrade
    # gracefully). On Windows they're kept (drag-drop works there).
    excludes=["numpy", "scipy", "matplotlib", "IPython", "jupyter",
              "audiohelper.highgrabber", "playwright", "httpx", "keyring",
              "pytest", "_pytest"] + _MAC_EXCLUDE,
    noarchive=False,
)

pyz = PYZ(a.pure)

# ── Mac build — .app bundle ───────────────────────────────────────────────────
if IS_MAC:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="TradersLittleJedi",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,          # UPX can trigger Gatekeeper on Mac — leave off
        console=False,      # No Terminal window
        target_arch=_MAC_ARCH,      # universal2 in CI (Intel + Apple Silicon)
        icon=str(ROOT / "assets" / "icon.icns"),
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="TradersLittleJedi",
    )
    app = BUNDLE(
        coll,
        name="Trader's Little Jedi.app",
        icon=str(ROOT / "assets" / "icon.icns"),
        bundle_identifier="com.auxren.audiohelper",
        info_plist={
            "CFBundleName":             "Trader's Little Jedi",
            "CFBundleDisplayName":      "Trader's Little Jedi",
            "CFBundleShortVersionString": "0.2.2",
            "CFBundleVersion":          "0.2.2",
            "CFBundleExecutable":       "TradersLittleJedi",
            "NSHighResolutionCapable":  True,
            "NSRequiresAquaSystemAppearance": False,   # supports dark mode
            "LSMinimumSystemVersion":   "12.0",
            "NSHumanReadableCopyright": "© 2026 auxren",
            # Allows access to external volumes (FastDrive etc.)
            "NSDocumentsFolderUsageDescription": "Access audio files for processing.",
            "NSDownloadsFolderUsageDescription": "Save processed audio files.",
        },
    )

# ── Windows build — single .exe ───────────────────────────────────────────────
elif IS_WIN:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="TradersLittleJedi",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,              # No console window
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,     # set via build_windows.ps1
        entitlements_file=None,
        icon=str(ROOT / "assets" / "icon.ico"),
        version_file=str(ROOT / "assets" / "version_info.txt") if
                     (ROOT / "assets" / "version_info.txt").exists() else None,
    )
