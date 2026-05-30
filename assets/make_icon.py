#!/usr/bin/env python3
"""Generate app icons for Trader's Little Jedi.

Creates:
  assets/icon_1024.png  — source PNG (all platforms)
  assets/icon.ico       — Windows multi-size icon
  assets/icon.icns      — macOS icon bundle (requires iconutil, Mac only)

Run:  python assets/make_icon.py
Requires: Pillow  (pip install Pillow)
"""

import struct
import sys
import zlib
from pathlib import Path

ASSETS = Path(__file__).resolve().parent

try:
    from PIL import Image, ImageDraw, ImageFilter
    _PIL = True
except ImportError:
    _PIL = False


def _draw_icon(size: int) -> "Image.Image":
    """Draw the TLJ icon at *size* x *size* pixels."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    # ── Background: dark navy rounded square ──────────────────────────────────
    bg_color = (13, 17, 23, 255)       # #0d1117
    r = max(2, size // 7)              # corner radius
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=bg_color)

    # ── Waveform bars ─────────────────────────────────────────────────────────
    bar_heights = [0.30, 0.55, 0.80, 1.00, 0.85, 0.60, 0.75, 0.45, 0.25]
    n = len(bar_heights)
    pad       = size * 0.15
    bar_area_w = size - 2 * pad
    bar_area_h = size * 0.55
    gap_ratio  = 0.35                  # fraction of slot that is the gap
    slot_w     = bar_area_w / n
    bar_w      = max(1, slot_w * (1 - gap_ratio))
    center_y   = size * 0.52

    glow_color = (61, 143, 209, 60)    # dim blue glow
    bar_color  = (61, 143, 209, 255)   # #3d8fd1

    for i, h in enumerate(bar_heights):
        x0 = pad + i * slot_w + (slot_w - bar_w) / 2
        x1 = x0 + bar_w
        bh = h * bar_area_h / 2

        # Soft glow (wider, semi-transparent rectangle)
        glow = max(1, bar_w * 1.8)
        gx0  = pad + i * slot_w + (slot_w - glow) / 2
        d.rectangle([gx0, center_y - bh * 1.1,
                     gx0 + glow, center_y + bh * 1.1],
                    fill=glow_color)
        # Bar
        d.rectangle([x0, center_y - bh, x1, center_y + bh], fill=bar_color)

    # ── Subtle bottom label: "TLJ" at small sizes is unreadable — skip ────────

    return img


def make_png(size: int = 1024) -> Path:
    img  = _draw_icon(size)
    path = ASSETS / "icon_1024.png"
    img.save(path, "PNG")
    print(f"  Saved {path}")
    return path


def make_ico() -> Path:
    """Windows .ico with 16/32/48/64/128/256 px variants."""
    sizes = [16, 32, 48, 64, 128, 256]
    images = [_draw_icon(s) for s in sizes]
    path = ASSETS / "icon.ico"
    images[0].save(path, format="ICO",
                   sizes=[(s, s) for s in sizes],
                   append_images=images[1:])
    print(f"  Saved {path}")
    return path


def make_icns() -> Path:
    """macOS .icns via iconutil (Mac only)."""
    import subprocess, shutil
    if not shutil.which("iconutil"):
        print("  iconutil not found — skipping .icns (Mac only)")
        return ASSETS / "icon.icns"

    iconset = ASSETS / "icon.iconset"
    iconset.mkdir(exist_ok=True)

    spec = {
        "icon_16x16.png":       16,
        "icon_16x16@2x.png":    32,
        "icon_32x32.png":       32,
        "icon_32x32@2x.png":    64,
        "icon_128x128.png":     128,
        "icon_128x128@2x.png":  256,
        "icon_256x256.png":     256,
        "icon_256x256@2x.png":  512,
        "icon_512x512.png":     512,
        "icon_512x512@2x.png":  1024,
    }
    for fname, sz in spec.items():
        img = _draw_icon(sz)
        img.save(iconset / fname, "PNG")

    path = ASSETS / "icon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)],
                   check=True)
    import shutil as _sh
    _sh.rmtree(iconset, ignore_errors=True)
    print(f"  Saved {path}")
    return path


if __name__ == "__main__":
    if not _PIL:
        print("ERROR: Pillow not installed.  Run:  pip install Pillow")
        sys.exit(1)
    print("Generating icons...")
    make_png(1024)
    make_ico()
    if sys.platform == "darwin":
        make_icns()
    else:
        print("  (icon.icns will be generated on Mac at build time)")
    print("Done.")
