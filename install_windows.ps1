#Requires -Version 5.1
<#
.SYNOPSIS
    Trader's Little Jedi — Windows installer
.DESCRIPTION
    Installs Python, required Python packages, CLI tools (ffmpeg, flac,
    metaflac), and creates a Desktop shortcut.  Run as a normal user;
    UAC will prompt only if Python needs to be installed system-wide.
#>

$ErrorActionPreference = "Stop"
$AppName   = "Trader's Little Jedi"
$AppDir    = $PSScriptRoot
$ToolsDir  = Join-Path $AppDir "tools"
$TempDir   = $env:TEMP

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Write-Step { param([string]$msg) Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok   { param([string]$msg) Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn { param([string]$msg) Write-Host "    !!  $msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$msg) Write-Host "    XX  $msg" -ForegroundColor Red }

# ── Refresh PATH from registry (needed after winget installs Python) ──────────
function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path","Machine")
    $user    = [Environment]::GetEnvironmentVariable("Path","User")
    $env:Path = "$machine;$user"
}

# ── Find a working Python 3.11+ executable ───────────────────────────────────
function Find-Python {
    foreach ($cmd in @("python","python3","py")) {
        $p = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $p) { continue }
        $raw = & $p.Source --version 2>&1
        if ($raw -match "Python (\d+)\.(\d+)") {
            $maj = [int]$Matches[1]; $min = [int]$Matches[2]
            if ($maj -ge 3 -and $min -ge 11) { return $p.Source }
        }
    }
    return $null
}

# ── Download with progress ────────────────────────────────────────────────────
function Download-File {
    param([string]$Url, [string]$Dest, [string]$Label)
    Write-Host "    Downloading $Label …" -NoNewline
    $wc = New-Object System.Net.WebClient
    $wc.DownloadFile($Url, $Dest)
    Write-Host " done ($([math]::Round((Get-Item $Dest).Length/1MB,1)) MB)"
}

# ── Extract a single file from a ZIP ─────────────────────────────────────────
function Extract-FromZip {
    param([string]$ZipPath, [string]$NameFilter, [string]$DestDir)
    $zip = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $entry = $zip.Entries | Where-Object { $_.Name -like $NameFilter } | Select-Object -First 1
        if (-not $entry) { throw "Could not find '$NameFilter' inside $ZipPath" }
        $dest = Join-Path $DestDir $entry.Name
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $dest, $true)
        return $dest
    } finally {
        $zip.Dispose()
    }
}

# ── Get latest GitHub release asset URL ──────────────────────────────────────
function Get-GithubRelease {
    param([string]$Repo, [string]$AssetPattern)
    $url  = "https://api.github.com/repos/$Repo/releases/latest"
    $rel  = Invoke-RestMethod $url -Headers @{ "User-Agent" = "AudioHelper-Installer" }
    $asset = $rel.assets | Where-Object { $_.name -like $AssetPattern } | Select-Object -First 1
    if (-not $asset) { throw "No asset matching '$AssetPattern' in $Repo releases" }
    return $asset.browser_download_url
}

# =============================================================================
Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "  ║   Trader's Little Jedi  —  Installer    ║" -ForegroundColor Magenta
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Magenta
Write-Host ""
Write-Host "  App folder:  $AppDir"
Write-Host "  Tools folder: $ToolsDir"
Write-Host ""

# ─── Step 1: Python ──────────────────────────────────────────────────────────
Write-Step "Checking Python 3.11+…"
$python = Find-Python
if (-not $python) {
    Write-Warn "Python 3.11+ not found. Installing via winget…"
    try {
        winget install -e --id Python.Python.3 --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $python = Find-Python
    } catch {
        Write-Fail "winget failed: $_"
        Write-Host ""
        Write-Host "  Please install Python 3.11+ manually from  https://www.python.org/downloads/"
        Write-Host "  Then re-run this installer."
        Read-Host "`n  Press Enter to exit"
        exit 1
    }
}
if (-not $python) {
    Write-Fail "Python still not found after install. Please install manually and re-run."
    Read-Host "`n  Press Enter to exit"; exit 1
}
$pyVer = & $python --version 2>&1
Write-Ok "$python  ($pyVer)"

# ─── Step 2: pip packages ────────────────────────────────────────────────────
Write-Step "Installing Python packages (mutagen, tkinterdnd2)…"
& $python -m pip install --upgrade pip --quiet
& $python -m pip install mutagen tkinterdnd2 --quiet
Write-Ok "Packages installed."

# ─── Step 3: ffmpeg + ffprobe ────────────────────────────────────────────────
Write-Step "Checking ffmpeg…"
$needFfmpeg = (-not (Test-Path (Join-Path $ToolsDir "ffmpeg.exe"))) -and
              (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue))
if ($needFfmpeg) {
    Write-Host "    Fetching latest ffmpeg release URL from gyan.dev…"
    try {
        $ffmpegIndex = Invoke-RestMethod "https://www.gyan.dev/ffmpeg/builds/release-version" `
                           -Headers @{ "User-Agent" = "AudioHelper-Installer" }
        $ver = $ffmpegIndex.Trim()
        $ffmpegUrl = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-$ver-essentials_build.zip"
    } catch {
        # Fallback to the stable "latest" permalink
        $ffmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    }
    $ffmpegZip = Join-Path $TempDir "ffmpeg_essentials.zip"
    Download-File $ffmpegUrl $ffmpegZip "ffmpeg"
    Extract-FromZip $ffmpegZip "ffmpeg.exe"  $ToolsDir | Out-Null
    Extract-FromZip $ffmpegZip "ffprobe.exe" $ToolsDir | Out-Null
    Remove-Item $ffmpegZip -Force
    Write-Ok "ffmpeg + ffprobe installed to tools\"
} else {
    Write-Ok "ffmpeg already present — skipping download."
}

# ─── Step 4: flac + metaflac ─────────────────────────────────────────────────
Write-Step "Checking flac + metaflac…"
$needFlac = (-not (Test-Path (Join-Path $ToolsDir "flac.exe"))) -and
            (-not (Get-Command flac -ErrorAction SilentlyContinue))
if ($needFlac) {
    Write-Host "    Fetching latest FLAC release from GitHub…"
    try {
        $flacUrl = Get-GithubRelease "xiph/flac" "*win64*"
    } catch {
        # Hardcode a known good release if the API fails
        $flacUrl = "https://github.com/xiph/flac/releases/download/1.4.3/flac-1.4.3-win.zip"
    }
    $flacZip = Join-Path $TempDir "flac_win.zip"
    Download-File $flacUrl $flacZip "flac"
    # The release zip has files in a versioned subfolder; find by name
    $zip = [IO.Compression.ZipFile]::OpenRead($flacZip)
    try {
        foreach ($name in @("flac.exe","metaflac.exe")) {
            $entry = $zip.Entries | Where-Object { $_.Name -eq $name } | Select-Object -First 1
            if ($entry) {
                [IO.Compression.ZipFileExtensions]::ExtractToFile(
                    $entry, (Join-Path $ToolsDir $name), $true)
            }
        }
    } finally { $zip.Dispose() }
    Remove-Item $flacZip -Force
    Write-Ok "flac + metaflac installed to tools\"
} else {
    Write-Ok "flac already present — skipping download."
}

# ─── Step 5: Desktop shortcut ────────────────────────────────────────────────
Write-Step "Creating Desktop shortcut…"
$desktop  = [Environment]::GetFolderPath("Desktop")
$lnkPath  = Join-Path $desktop "$AppName.lnk"
$targetCmd = Join-Path $AppDir "TradersLittleJedi.cmd"

$wsh = New-Object -ComObject WScript.Shell
$sc  = $wsh.CreateShortcut($lnkPath)
$sc.TargetPath       = $targetCmd
$sc.WorkingDirectory = $AppDir
$sc.Description      = $AppName
$sc.Save()
Write-Ok "Shortcut created: $lnkPath"

# ─── Done ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  ┌──────────────────────────────────────────────┐" -ForegroundColor Green
Write-Host "  │  Installation complete!                      │" -ForegroundColor Green
Write-Host "  │  Double-click the shortcut on your Desktop   │" -ForegroundColor Green
Write-Host "  │  or run  TradersLittleJedi.cmd  to launch.   │" -ForegroundColor Green
Write-Host "  └──────────────────────────────────────────────┘" -ForegroundColor Green
Write-Host ""
$launch = Read-Host "  Launch Trader's Little Jedi now? [Y/n]"
if ($launch -ne "n" -and $launch -ne "N") {
    Start-Process $targetCmd
}
