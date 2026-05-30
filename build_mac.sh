#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Trader's Little Jedi — macOS build script
#
# Produces:
#   dist/Trader's Little Jedi.app   — signed & notarized .app bundle
#   dist/TradersLittleJedi-mac.dmg  — distributable disk image
#
# Prerequisites (fill in your Apple Developer details below):
#   - Xcode Command Line Tools:  xcode-select --install
#   - brew install python-tk ffmpeg flac create-dmg
#   - pip install pyinstaller pillow mutagen
#   - Apple Developer account with "Developer ID Application" certificate
#
# Usage:
#   bash build_mac.sh [--skip-sign] [--skip-notarize]
# ─────────────────────────────────────────────────────────────────────────────
set -eo pipefail

# ── Configuration — fill these in ────────────────────────────────────────────
APPLE_ID=""              # your Apple ID email, e.g. you@example.com
TEAM_ID=""               # 10-char team ID from developer.apple.com
APP_PASSWORD=""          # app-specific password from appleid.apple.com
SIGN_IDENTITY=""         # e.g. "Developer ID Application: Your Name (TEAMID)"
# ─────────────────────────────────────────────────────────────────────────────

APP_NAME="Trader's Little Jedi"
BUNDLE_ID="com.auxren.audiohelper"
VERSION="0.1.0"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST="$APP_DIR/dist"
APP_BUNDLE="$DIST/$APP_NAME.app"
DMG_PATH="$DIST/TradersLittleJedi-mac.dmg"

SKIP_SIGN=false
SKIP_NOTARIZE=false
for arg in "$@"; do
    [[ "$arg" == "--skip-sign"      ]] && SKIP_SIGN=true
    [[ "$arg" == "--skip-notarize"  ]] && SKIP_NOTARIZE=true
done

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${CYAN}==> $*${NC}"; }
ok()   { echo -e "    ${GREEN}OK${NC}  $*"; }
warn() { echo -e "    ${YELLOW}!!${NC}  $*"; }

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║  Trader's Little Jedi — Mac Build       ║"
echo "  ╚══════════════════════════════════════════╝"
echo "  Version: $VERSION"
echo ""

# ── Step 1: Python environment ────────────────────────────────────────────────
step "Setting up Python environment..."
VENV="$APP_DIR/.venv"
if [[ ! -d "$VENV" ]]; then
    python3 -m venv "$VENV"
fi
PY="$VENV/bin/python3"
PIP="$VENV/bin/pip"
"$PIP" install --upgrade pip --quiet
"$PIP" install pyinstaller pillow mutagen --quiet
ok "Dependencies ready."

# ── Step 2: Generate icons ────────────────────────────────────────────────────
step "Generating icons..."
"$PY" "$APP_DIR/assets/make_icon.py"
if [[ ! -f "$APP_DIR/assets/icon.icns" ]]; then
    echo "  ERROR: icon.icns not created. Check assets/make_icon.py output."
    exit 1
fi
ok "Icons ready."

# ── Step 3: Clean previous build ──────────────────────────────────────────────
step "Cleaning previous build..."
rm -rf "$APP_DIR/build" "$APP_DIR/dist"
ok "Clean."

# ── Step 4: PyInstaller ───────────────────────────────────────────────────────
step "Running PyInstaller..."
cd "$APP_DIR"
"$VENV/bin/pyinstaller" TradersLittleJedi.spec --noconfirm --clean
if [[ ! -d "$APP_BUNDLE" ]]; then
    echo "  ERROR: Build failed — $APP_BUNDLE not found."
    exit 1
fi
ok "App bundle created: $APP_BUNDLE"

# ── Step 5: Code signing ──────────────────────────────────────────────────────
if [[ "$SKIP_SIGN" == "true" ]]; then
    warn "Skipping code signing (--skip-sign)."
elif [[ -z "$SIGN_IDENTITY" ]]; then
    warn "SIGN_IDENTITY not set — skipping code signing."
    warn "Set it at the top of this script and re-run for a distributable build."
else
    step "Code signing..."
    # Sign all binaries inside the bundle first, then the bundle itself
    find "$APP_BUNDLE/Contents/MacOS" -type f -exec \
        codesign --force --sign "$SIGN_IDENTITY" \
                 --options runtime \
                 --timestamp {} \;
    find "$APP_BUNDLE/Contents/Frameworks" -name "*.dylib" -o -name "*.so" 2>/dev/null | \
        xargs -I{} codesign --force --sign "$SIGN_IDENTITY" \
                             --options runtime --timestamp {} 2>/dev/null || true
    codesign --deep --force --sign "$SIGN_IDENTITY" \
             --options runtime \
             --timestamp \
             --entitlements "$APP_DIR/assets/entitlements.plist" \
             "$APP_BUNDLE"
    codesign --verify --deep --strict "$APP_BUNDLE" && ok "Signature valid." || \
        { echo "  ERROR: Signature verification failed."; exit 1; }
fi

# ── Step 6: Create DMG ───────────────────────────────────────────────────────
step "Creating DMG..."
rm -f "$DMG_PATH"
if command -v create-dmg &>/dev/null; then
    create-dmg \
        --volname "$APP_NAME" \
        --volicon "$APP_DIR/assets/icon.icns" \
        --window-pos 200 120 \
        --window-size 600 400 \
        --icon-size 128 \
        --icon "$APP_NAME.app" 150 185 \
        --hide-extension "$APP_NAME.app" \
        --app-drop-link 450 185 \
        --background "$APP_DIR/assets/dmg_background.png" \
        "$DMG_PATH" \
        "$DIST/"
else
    # Fallback: plain hdiutil DMG
    hdiutil create -volname "$APP_NAME" \
                   -srcfolder "$DIST/$APP_NAME.app" \
                   -ov -format UDZO \
                   "$DMG_PATH"
fi
ok "DMG created: $DMG_PATH"

# ── Step 7: Notarize ─────────────────────────────────────────────────────────
if [[ "$SKIP_NOTARIZE" == "true" ]]; then
    warn "Skipping notarization (--skip-notarize)."
elif [[ -z "$APPLE_ID" || -z "$TEAM_ID" || -z "$APP_PASSWORD" ]]; then
    warn "APPLE_ID / TEAM_ID / APP_PASSWORD not set — skipping notarization."
    warn "Set them at the top of this script for a fully trusted build."
else
    step "Submitting for notarization (this takes 1-5 minutes)..."
    xcrun notarytool submit "$DMG_PATH" \
        --apple-id  "$APPLE_ID" \
        --team-id   "$TEAM_ID" \
        --password  "$APP_PASSWORD" \
        --wait
    xcrun stapler staple "$DMG_PATH"
    ok "Notarization complete. DMG is stapled."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  Build complete!                                         │"
echo "  │                                                          │"
echo "  │  App:  dist/Trader's Little Jedi.app                    │"
echo "  │  DMG:  dist/TradersLittleJedi-mac.dmg                   │"
echo "  └──────────────────────────────────────────────────────────┘"
echo ""
