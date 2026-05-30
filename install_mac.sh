#!/usr/bin/env bash
# Trader's Little Jedi — macOS installer
# Run from the AudioHelper folder:  bash install_mac.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Trader's Little Jedi"

# ── Colours ───────────────────────────────────────────────────────────────────
CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
step()  { echo -e "\n${CYAN}==> $*${NC}"; }
ok()    { echo -e "    ${GREEN}OK${NC}  $*"; }
warn()  { echo -e "    ${YELLOW}!!${NC}  $*"; }
fail()  { echo -e "    ${RED}XX${NC}  $*"; }

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║   Trader's Little Jedi  —  Installer    ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""
echo "  App folder: $APP_DIR"
echo ""

# ─── Step 1: Homebrew ─────────────────────────────────────────────────────────
step "Checking Homebrew…"
if ! command -v brew &>/dev/null; then
    warn "Homebrew not found. Installing…"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for Apple Silicon
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi
ok "Homebrew $(brew --version | head -1)"

# ─── Step 2: Python with tkinter ─────────────────────────────────────────────
step "Checking Python + tkinter…"

# Detect which python-tk version to install (matches the python3 brew formula)
PY_FORMULA=$(brew list --formula 2>/dev/null | grep -E '^python@[0-9.]+$' | sort -V | tail -1)
if [[ -z "$PY_FORMULA" ]]; then
    PY_FORMULA="python@3.13"
fi
TK_FORMULA="python-tk@${PY_FORMULA#python@}"

if ! brew list "$TK_FORMULA" &>/dev/null; then
    warn "Installing $TK_FORMULA…"
    brew install "$TK_FORMULA"
fi
ok "$TK_FORMULA installed."

# Find the python3 that matches the brew formula
PYTHON3=$(brew --prefix "$PY_FORMULA")/bin/python3 2>/dev/null || true
if [[ ! -x "$PYTHON3" ]]; then
    PYTHON3=$(command -v python3 || true)
fi
if [[ -z "$PYTHON3" ]]; then
    fail "python3 not found even after Homebrew install."
    exit 1
fi

PY_VER=$("$PYTHON3" --version 2>&1)
ok "$PYTHON3  ($PY_VER)"

# Quick tkinter sanity check
if ! "$PYTHON3" -c "import tkinter" 2>/dev/null; then
    warn "tkinter still not importable — trying brew link…"
    brew link --force "$TK_FORMULA" 2>/dev/null || true
    if ! "$PYTHON3" -c "import tkinter" 2>/dev/null; then
        fail "tkinter import failed. Please report this at https://github.com/auxren/AudioHelper/issues"
        exit 1
    fi
fi
ok "tkinter OK."

# ─── Step 3: CLI tools ────────────────────────────────────────────────────────
step "Installing CLI tools (ffmpeg, flac)…"
BREW_INSTALL=()
command -v ffmpeg   &>/dev/null || BREW_INSTALL+=("ffmpeg")
command -v flac     &>/dev/null || BREW_INSTALL+=("flac")

if [[ ${#BREW_INSTALL[@]} -gt 0 ]]; then
    brew install "${BREW_INSTALL[@]}"
fi
ok "ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
ok "flac:   $(flac --version 2>&1 | head -1)"

# ─── Step 4: Python packages ─────────────────────────────────────────────────
step "Installing Python packages (mutagen, tkinterdnd2)…"
"$PYTHON3" -m pip install --upgrade pip --quiet
"$PYTHON3" -m pip install mutagen tkinterdnd2 --quiet
ok "mutagen and tkinterdnd2 installed."

# ─── Step 5: Desktop launcher ────────────────────────────────────────────────
step "Creating Desktop launcher…"
LAUNCHER="$HOME/Desktop/${APP_NAME}.command"
cat > "$LAUNCHER" <<SCRIPT
#!/usr/bin/env bash
cd "$APP_DIR"
exec "$PYTHON3" TradersLittleJedi.py
SCRIPT
chmod +x "$LAUNCHER"
ok "Launcher created: $LAUNCHER"

# ─── Step 6: (Optional) .app bundle in Applications ──────────────────────────
step "Creating Applications/.app bundle…"
APP_BUNDLE="/Applications/${APP_NAME}.app"
MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
mkdir -p "$MACOS_DIR"

cat > "$MACOS_DIR/${APP_NAME}" <<SCRIPT
#!/usr/bin/env bash
cd "$APP_DIR"
exec "$PYTHON3" TradersLittleJedi.py
SCRIPT
chmod +x "$MACOS_DIR/${APP_NAME}"

cat > "$APP_BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>com.auxren.audiohelper</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

ok ".app bundle created: $APP_BUNDLE"

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "  ┌──────────────────────────────────────────────────────────┐"
echo "  │  Installation complete!                                  │"
echo "  │                                                          │"
echo "  │  Launch options:                                         │"
echo "  │    • Double-click '${APP_NAME}' on your Desktop   │"
echo "  │    • Open from Applications (Finder)                     │"
echo "  │    • Run:  python3 TradersLittleJedi.py                  │"
echo "  └──────────────────────────────────────────────────────────┘"
echo ""

read -rp "  Launch now? [Y/n] " answer
if [[ "$answer" != "n" && "$answer" != "N" ]]; then
    open "$APP_BUNDLE" 2>/dev/null || "$PYTHON3" TradersLittleJedi.py &
fi
