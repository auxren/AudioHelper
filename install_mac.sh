#!/usr/bin/env bash
# Trader's Little Jedi — macOS installer
# Run from the AudioHelper folder:  bash install_mac.sh

set -eo pipefail

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
step "Checking Python + tkinter..."

# Find the highest python@X.Y formula already installed via brew.
# 'grep' may return nothing (exit 1) so we use '|| true' to avoid killing
# the script under 'set -eo pipefail'.
PY_FORMULA=$(brew list --formula 2>/dev/null | grep -E '^python@[0-9.]+$' | sort -V | tail -1 || true)
if [[ -z "$PY_FORMULA" ]]; then
    PY_FORMULA="python@3.13"
fi
PY_VERSION="${PY_FORMULA#python@}"   # e.g. "3.14"
TK_FORMULA="python-tk@${PY_VERSION}"

if ! brew list "$TK_FORMULA" &>/dev/null 2>&1; then
    warn "Installing $TK_FORMULA..."
    brew install "$TK_FORMULA" || {
        warn "$TK_FORMULA not available; trying generic python-tk..."
        brew install python-tk || true
    }
fi
ok "$TK_FORMULA present."

# Find the matching python3 binary
PYTHON3=""
if brew --prefix "$PY_FORMULA" &>/dev/null 2>&1; then
    CANDIDATE="$(brew --prefix "$PY_FORMULA")/bin/python3"
    [[ -x "$CANDIDATE" ]] && PYTHON3="$CANDIDATE"
fi
if [[ -z "$PYTHON3" ]]; then
    PYTHON3=$(command -v python3 || true)
fi
if [[ -z "$PYTHON3" ]]; then
    fail "python3 not found even after Homebrew install."
    exit 1
fi

PY_VER=$("$PYTHON3" --version 2>&1)
ok "$PYTHON3  ($PY_VER)"

# Verify tkinter is importable
if ! "$PYTHON3" -c "import tkinter" 2>/dev/null; then
    warn "tkinter not importable -- trying brew link..."
    brew link --force "$TK_FORMULA" 2>/dev/null || true
    if ! "$PYTHON3" -c "import tkinter" 2>/dev/null; then
        fail "tkinter import failed. Please file an issue at https://github.com/auxren/AudioHelper/issues"
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

# ─── Step 4: Virtual environment + Python packages ───────────────────────────
step "Creating virtual environment and installing packages..."
VENV_DIR="$APP_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON3" -m venv "$VENV_DIR"
fi
VENV_PYTHON="$VENV_DIR/bin/python3"
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
"$VENV_PYTHON" -m pip install mutagen tkinterdnd2 --quiet
ok "Virtual environment ready: $VENV_DIR"
ok "mutagen and tkinterdnd2 installed."

# From here on, launchers use the venv Python so packages are always available.

# ─── Step 5: Desktop launcher ────────────────────────────────────────────────
step "Creating Desktop launcher..."
LAUNCHER="$HOME/Desktop/${APP_NAME}.command"
cat > "$LAUNCHER" <<SCRIPT
#!/usr/bin/env bash
cd "$APP_DIR"
exec "$VENV_DIR/bin/python3" TradersLittleJedi.py
SCRIPT
chmod +x "$LAUNCHER"
ok "Launcher created: $LAUNCHER"

# ─── Step 6: .app bundle in Applications ─────────────────────────────────────
step "Creating Applications/.app bundle..."
APP_BUNDLE="/Applications/${APP_NAME}.app"
MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
mkdir -p "$MACOS_DIR"

cat > "$MACOS_DIR/${APP_NAME}" <<SCRIPT
#!/usr/bin/env bash
cd "$APP_DIR"
exec "$VENV_DIR/bin/python3" TradersLittleJedi.py
SCRIPT
chmod +x "$MACOS_DIR/${APP_NAME}"

# Copy icon into the bundle
if [[ -f "$APP_DIR/assets/icon.icns" ]]; then
    cp "$APP_DIR/assets/icon.icns" "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
fi

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
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSRequiresAquaSystemAppearance</key>
    <false/>
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
