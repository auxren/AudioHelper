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


def _hex(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _lerp_color(a: tuple, b: tuple, t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _draw_icon(size: int) -> "Image.Image":
    """Draw the TLJ icon — Dead-inspired gradient waveform on dark purple."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    # ── Background: dark purple rounded square (ConcertTagger backgroundGradient)
    bg = (15, 11, 26, 255)   # #0F0B1A
    r  = max(2, size // 6)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=bg)

    # ── Subtle radial glow centre ──────────────────────────────────────────────
    glow_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_img)
    glow_r = size // 2
    for step in range(20, 0, -1):
        alpha = int(30 * step / 20)
        gs = int(glow_r * step / 20)
        cx, cy = size // 2, size // 2
        gd.ellipse([cx - gs, cy - gs, cx + gs, cy + gs],
                   fill=(119, 68, 221, alpha))
    img = Image.alpha_composite(img, glow_img)
    d = ImageDraw.Draw(img)

    # ── Waveform bars (funGradient1: Purple → Blue → Pink) ───────────────────
    bar_heights = [0.28, 0.50, 0.78, 0.95, 1.00, 0.88, 0.70, 0.55, 0.35]
    n          = len(bar_heights)
    pad        = size * 0.13
    bar_area_w = size - 2 * pad
    bar_area_h = size * 0.56
    gap_ratio  = 0.32
    slot_w     = bar_area_w / n
    bar_w      = max(1, slot_w * (1 - gap_ratio))
    center_y   = size * 0.52

    # funGradient1 colors
    c_purple = _hex("#9933CC")
    c_blue   = _hex("#3366E6")
    c_pink   = _hex("#FF6699")

    for i, h in enumerate(bar_heights):
        t    = i / (n - 1)
        col  = (_lerp_color(c_purple, c_blue, min(1, t * 2))
                if t < 0.5 else
                _lerp_color(c_blue, c_pink, (t - 0.5) * 2))
        col_rgba  = (*col, 255)
        glow_rgba = (*col, 55)

        x0 = pad + i * slot_w + (slot_w - bar_w) / 2
        x1 = x0 + bar_w
        bh = h * bar_area_h / 2

        # Glow
        gw  = max(1, bar_w * 2.0)
        gx0 = pad + i * slot_w + (slot_w - gw) / 2
        d.rectangle([gx0, center_y - bh * 1.15, gx0 + gw, center_y + bh * 1.15],
                    fill=glow_rgba)
        # Bar (rounded caps at large sizes)
        if size >= 128:
            br = max(1, int(bar_w * 0.35))
            d.rounded_rectangle([x0, center_y - bh, x1, center_y + bh],
                                 radius=br, fill=col_rgba)
        else:
            d.rectangle([x0, center_y - bh, x1, center_y + bh], fill=col_rgba)

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
