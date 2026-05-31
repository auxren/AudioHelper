#Requires -Version 5.1
<#
.SYNOPSIS
    Trader's Little Jedi — Windows build script
.DESCRIPTION
    Produces:
      dist\TradersLittleJedi.exe              — standalone executable
      dist\TradersLittleJedi-Setup-1.0.0.exe  — Inno Setup installer (signed)

    Prerequisites:
      - Python 3.11+  (winget install Python.Python.3)
      - Inno Setup 6  (winget install JRSoftware.InnoSetup)
      - Optional: signtool.exe in PATH (from Windows SDK)

    Fill in SIGN_CERT_PATH / SIGN_CERT_PASSWORD for code signing.

.PARAMETER SkipSign
    Skip code signing (produces an unsigned build).
.PARAMETER SkipInstaller
    Skip Inno Setup packaging step.
#>

param(
    [switch]$SkipSign,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

# ── Configuration — fill these in ────────────────────────────────────────────
$SIGN_CERT_PATH     = ""   # path to .pfx certificate, e.g. "C:\certs\myapp.pfx"
$SIGN_CERT_PASSWORD = ""   # certificate password (or leave blank to prompt)
$SIGN_TIMESTAMP_URL = "http://timestamp.digicert.com"
# ─────────────────────────────────────────────────────────────────────────────

$APP_NAME    = "Trader's Little Jedi"
$APP_VERSION = "0.2.0"
$APP_DIR     = $PSScriptRoot
$DIST        = "$APP_DIR\dist"

function Write-Step { param([string]$msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$msg) Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "    !!  $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "  ║  Trader's Little Jedi — Windows Build   ║" -ForegroundColor Magenta
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Magenta
Write-Host "  Version: $APP_VERSION"
Write-Host ""

# ── Step 1: Python and venv ───────────────────────────────────────────────────
Write-Step "Setting up Python environment..."
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw "Python not found. Run Install Windows.bat first." }

$venv = "$APP_DIR\.venv"
if (-not (Test-Path $venv)) {
    & python -m venv $venv
}
$pip = "$venv\Scripts\pip.exe"
$py  = "$venv\Scripts\python.exe"
& $pip install --upgrade pip --quiet
& $pip install pyinstaller pillow mutagen --quiet
Write-Ok "Dependencies ready."

# ── Step 2: Generate icons ────────────────────────────────────────────────────
Write-Step "Generating icons..."
& $py "$APP_DIR\assets\make_icon.py"
if (-not (Test-Path "$APP_DIR\assets\icon.ico")) {
    throw "icon.ico not created. Check assets\make_icon.py output."
}
Write-Ok "Icons ready."

# ── Step 3: Generate Windows version info ────────────────────────────────────
Write-Step "Writing version info..."
$verParts = $APP_VERSION -split '\.'
$vi = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($($verParts[0]),$($verParts[1]),$($verParts[2] ?? 0),0),
    prodvers=($($verParts[0]),$($verParts[1]),$($verParts[2] ?? 0),0),
  ),
  kids=[
    StringFileInfo([StringTable('040904B0',[
      StringStruct('CompanyName',      'auxren'),
      StringStruct('FileDescription',  "Trader's Little Jedi"),
      StringStruct('FileVersion',      '$APP_VERSION'),
      StringStruct('InternalName',     'TradersLittleJedi'),
      StringStruct('LegalCopyright',   '(c) 2026 auxren'),
      StringStruct('OriginalFilename', 'TradersLittleJedi.exe'),
      StringStruct('ProductName',      "Trader's Little Jedi"),
      StringStruct('ProductVersion',   '$APP_VERSION'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
$vi | Set-Content "$APP_DIR\assets\version_info.txt" -Encoding UTF8
Write-Ok "Version info written."

# ── Step 4: Clean previous build ─────────────────────────────────────────────
Write-Step "Cleaning previous build..."
Remove-Item -Recurse -Force "$APP_DIR\build" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$APP_DIR\dist"  -ErrorAction SilentlyContinue
Write-Ok "Clean."

# ── Step 5: PyInstaller ───────────────────────────────────────────────────────
Write-Step "Running PyInstaller..."
Set-Location $APP_DIR
& "$venv\Scripts\pyinstaller.exe" TradersLittleJedi.spec --noconfirm --clean
$exe = "$DIST\TradersLittleJedi.exe"
if (-not (Test-Path $exe)) {
    throw "Build failed — TradersLittleJedi.exe not found."
}
Write-Ok "Executable: $exe"

# ── Step 6: Code sign the .exe ───────────────────────────────────────────────
if ($SkipSign -or -not $SIGN_CERT_PATH -or -not (Test-Path $SIGN_CERT_PATH)) {
    Write-Warn "Skipping code signing (set SIGN_CERT_PATH at top of script)."
} else {
    Write-Step "Signing executable..."
    $signtool = Get-Command signtool -ErrorAction SilentlyContinue
    if (-not $signtool) {
        Write-Warn "signtool.exe not found — install Windows SDK."
    } else {
        $signArgs = @(
            "sign",
            "/f",  $SIGN_CERT_PATH,
            "/p",  $SIGN_CERT_PASSWORD,
            "/tr", $SIGN_TIMESTAMP_URL,
            "/td", "sha256",
            "/fd", "sha256",
            "/v",
            $exe
        )
        & signtool @signArgs
        Write-Ok "Executable signed."
    }
}

# ── Step 7: Inno Setup installer ─────────────────────────────────────────────
if ($SkipInstaller) {
    Write-Warn "Skipping installer (--SkipInstaller)."
} else {
    Write-Step "Building installer with Inno Setup..."
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if (-not $iscc) {
        $iscc_path = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
        if (Test-Path $iscc_path) { $iscc = $iscc_path }
    }
    if (-not $iscc) {
        Write-Warn "Inno Setup not found.  Install:  winget install JRSoftware.InnoSetup"
    } else {
        & $iscc "$APP_DIR\installer\setup.iss"
        $installer = "$DIST\TradersLittleJedi-Setup-$APP_VERSION.exe"
        if (Test-Path $installer) {
            if (-not $SkipSign -and $SIGN_CERT_PATH -and (Get-Command signtool -ErrorAction SilentlyContinue)) {
                Write-Step "Signing installer..."
                & signtool sign /f $SIGN_CERT_PATH /p $SIGN_CERT_PASSWORD `
                                /tr $SIGN_TIMESTAMP_URL /td sha256 /fd sha256 `
                                /v $installer
                Write-Ok "Installer signed."
            }
            Write-Ok "Installer: $installer"
        }
    }
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ┌──────────────────────────────────────────────────────────┐" -ForegroundColor Green
Write-Host "  │  Build complete!                                         │" -ForegroundColor Green
Write-Host "  │                                                          │" -ForegroundColor Green
Write-Host "  │  EXE:       dist\TradersLittleJedi.exe                  │" -ForegroundColor Green
Write-Host "  │  Installer: dist\TradersLittleJedi-Setup-$APP_VERSION.exe │" -ForegroundColor Green
Write-Host "  └──────────────────────────────────────────────────────────┘" -ForegroundColor Green
Write-Host ""
