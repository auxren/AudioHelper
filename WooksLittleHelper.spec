# -*- mode: python ; coding: utf-8 -*-
# Wook's Little Helper — the standalone one-button show chopper.
# macOS-only build target (the full app covers Windows).

import os
import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH)
VERSION = re.search(r'"([^"]+)"',
    (ROOT / "audiohelper" / "__init__.py").read_text()).group(1)

IS_MAC = sys.platform == "darwin"
_MAC_ARCH = "universal2" if (IS_MAC and os.environ.get("TLJ_UNIVERSAL")) else None

a = Analysis(
    [str(ROOT / "WooksLittleHelper.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "audiohelper"), "audiohelper")],
    hiddenimports=[
        "tkinter", "tkinter.ttk", "tkinter.filedialog", "_tkinter",
        "mutagen", "mutagen.flac", "mutagen.wave", "mutagen.aiff",
        "audiohelper.lite", "audiohelper.auto_align", "audiohelper.checksums",
        "audiohelper.live_tagger", "audiohelper.tc_tagger",
        "audiohelper.tools", "audiohelper.config",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["internetarchive"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WooksLittleHelper",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=_MAC_ARCH,
    icon=str(ROOT / "assets" / "icon.icns"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name="WooksLittleHelper")
app = BUNDLE(
    coll,
    name="Wook's Little Helper.app",
    icon=str(ROOT / "assets" / "icon.icns"),
    bundle_identifier="com.auxren.wookslittlehelper",
    info_plist={
        "CFBundleName":             "Wook's Little Helper",
        "CFBundleDisplayName":      "Wook's Little Helper",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion":          VERSION,
        "CFBundleExecutable":       "WooksLittleHelper",
        "NSHighResolutionCapable":  True,
        "NSRequiresAquaSystemAppearance": False,
        "LSMinimumSystemVersion":   "12.0",
        "NSHumanReadableCopyright": "© 2026 auxren",
        "NSDocumentsFolderUsageDescription": "Access audio files for processing.",
        "NSDownloadsFolderUsageDescription": "Save processed audio files.",
    },
)
