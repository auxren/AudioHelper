# HighGrabber (vendored, hidden)

This is a vendored copy of HighGrabber (github.com/auxren/HighGrabber) — a bulk
downloader for Hightail Spaces `receive/` links.

**It is intentionally NOT wired into the GUI.** The code is kept here so the
download functionality can be invoked programmatically or surfaced later, but
no tile, menu item, or button in Trader's Little Jedi opens it.

## Usage (programmatic / CLI only)

    python -m audiohelper.highgrabber login
    python -m audiohelper.highgrabber <hightail-url> -d ~/Downloads/concerts

## Dependencies

Not installed by the standard AudioHelper installer. To use HighGrabber:

    pip install "httpx[http2]>=0.27" "playwright>=1.40" "keyring>=24" \
                "platformdirs>=4" "rich>=13"
    playwright install chromium

These are kept out of requirements.txt so the GUI app stays lightweight.

## Why hidden

The workflow is: HighGrabber downloads concert archives → Trader's Little Jedi
splits/tags them. Keeping the download layer as importable code (rather than a
visible feature) preserves the option to integrate it without committing to a
download UI in the audio toolkit.
