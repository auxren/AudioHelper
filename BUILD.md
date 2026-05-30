# Building distributable apps

Trader's Little Jedi ships as a native app via PyInstaller — no Python
install required on the user's machine.

## macOS → `.app` + `.dmg`

### Quick dev build (unsigned, for local testing)
```bash
bash build_mac.sh --skip-sign --skip-notarize
```
Produces:
- `dist/Trader's Little Jedi.app` — double-clickable bundle with the icon
- `dist/TradersLittleJedi-mac.dmg` — ~20 MB disk image

An unsigned build runs locally but Gatekeeper warns other users
("unidentified developer" → right-click → Open).

### Full signed + notarized build (for distribution)
You have an Apple Developer account, so fill these in at the top of
`build_mac.sh`:

| Variable | Where to get it |
|---|---|
| `APPLE_ID` | your Apple ID email |
| `TEAM_ID` | developer.apple.com → Membership → Team ID (10 chars) |
| `APP_PASSWORD` | appleid.apple.com → Sign-In & Security → App-Specific Passwords |
| `SIGN_IDENTITY` | `security find-identity -v -p codesigning` → the "Developer ID Application: …" line |

Then:
```bash
bash build_mac.sh
```
This signs with the hardened runtime + entitlements, builds the DMG,
submits to Apple's notary service (`xcrun notarytool`, 1–5 min), and
staples the ticket. The result opens with zero Gatekeeper warnings.

First time only — create a "Developer ID Application" certificate in
Xcode (Settings → Accounts → Manage Certificates → +) or at
developer.apple.com → Certificates.

## Windows → `.exe` + installer

On a Windows machine:
```powershell
powershell -ExecutionPolicy Bypass -File build_windows.ps1
```
Produces:
- `dist\TradersLittleJedi.exe` — standalone executable
- `dist\TradersLittleJedi-Setup-1.0.0.exe` — Inno Setup installer

Requires Python 3.11+ and Inno Setup 6 (`winget install JRSoftware.InnoSetup`).

Code signing (to avoid SmartScreen "unknown publisher") needs a separate
Windows **code-signing certificate** (DigiCert/Sectigo, ~$200–400/yr) —
this is NOT the same as an Apple or Microsoft Store account. Fill in
`SIGN_CERT_PATH` / `SIGN_CERT_PASSWORD` at the top of `build_windows.ps1`.
Unsigned installers still work; users just see a SmartScreen prompt.

## How it's wired

- `TradersLittleJedi.spec` — PyInstaller spec (platform-aware: `.app`
  bundle on Mac, single `.exe` on Windows). Lazy-imported submodules
  (batch_convert, bulk_tagger) are listed in `hiddenimports`; the hidden
  HighGrabber subpackage + its heavy deps are in `excludes`.
- `assets/make_icon.py` — generates `icon.icns` / `icon.ico` from code.
- `assets/entitlements.plist` — hardened-runtime entitlements for notarization.
- `installer/setup.iss` — Inno Setup script (per-user install, Start Menu,
  desktop shortcut, uninstaller).

## Tests before building
```bash
bash run_tests.sh
```
72 headless tests; audio-touching ones auto-skip without ffmpeg/mutagen.
