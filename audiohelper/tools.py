import io
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


@dataclass
class Tool:
    name: str
    exe: str
    description: str
    homepage: str = ""
    version_args: list[str] = field(default_factory=lambda: ["--version"])
    version_re: Optional[str] = None
    upstream_check: Optional[Callable[[], Optional[str]]] = None
    upstream_install: Optional[Callable[[str], None]] = None
    # Known system install locations searched when not in tools\ or PATH
    system_paths: list[str] = field(default_factory=list)
    # True for GUI-only apps that have no version CLI flag; skips the subprocess probe
    gui_only: bool = False
    # True for abandoned tools with no upstream updates; shown grayed-out in the update window
    legacy: bool = False

    def path(self, config) -> Path:
        override = (config.get("tool_paths") or {}).get(self.name)
        if override:
            return Path(override)
        bundled = TOOLS_DIR / self.exe
        if bundled.exists():
            return bundled
        on_path = shutil.which(self.exe)
        if on_path:
            return Path(on_path)
        for sp in self.system_paths:
            p = Path(sp)
            if p.exists():
                return p
        return bundled  # non-existent; callers can detect via .exists()

    def current_version(self, config) -> Optional[str]:
        p = self.path(config)
        if not p.exists():
            return None
        if self.gui_only:
            return "(installed)"
        try:
            r = subprocess.run(
                [str(p), *self.version_args],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None
        text = (r.stdout or "") + "\n" + (r.stderr or "")
        if self.version_re:
            m = re.search(self.version_re, text)
            if m:
                return m.group(1)
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
        return first


# ============================================================
# Shared helpers
# ============================================================


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)) or (0,)


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "TradersLittleJedi/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _extract_one(zip_bytes: bytes, predicate: Callable[[str], bool], dest: Path) -> bool:
    """Extract the first archive member whose name satisfies predicate. Returns success."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for m in zf.namelist():
            if predicate(m):
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(m) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                return True
    return False


def _basename(member: str) -> str:
    return member.replace("\\", "/").rsplit("/", 1)[-1]


# ============================================================
# FLAC + metaflac  (xiph.org)
# ============================================================

_FLAC_LISTING = "https://ftp.osuosl.org/pub/xiph/releases/flac/"


def _flac_latest() -> Optional[str]:
    html = _http_get(_FLAC_LISTING, timeout=15).decode("utf-8", errors="replace")
    versions = sorted(set(re.findall(r"flac-(\d+\.\d+\.\d+)-win\.zip", html)), key=_vtuple)
    return versions[-1] if versions else None


def _flac_install(version: str) -> None:
    url = f"{_FLAC_LISTING}flac-{version}-win.zip"
    data = _http_get(url, timeout=180)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    wanted = {"flac.exe": False, "metaflac.exe": False}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = zf.namelist()
        for prefer_64 in (True, False):
            for target in list(wanted.keys()):
                if wanted[target]:
                    continue
                for m in members:
                    norm = m.replace("\\", "/").lower()
                    if not norm.endswith("/" + target):
                        continue
                    if prefer_64 and "win64" not in norm:
                        continue
                    with zf.open(m) as src, open(TOOLS_DIR / target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    wanted[target] = True
                    break
    if not wanted["flac.exe"]:
        raise RuntimeError(f"flac.exe not found inside {url}")


# ============================================================
# LAME  (rarewares.org — scrapes whatever current zip is linked)
# ============================================================

_RAREWARES_LAME = "https://www.rarewares.org/mp3-lame-bundle.php"


def _lame_find() -> tuple[Optional[str], Optional[str]]:
    """Return (version, absolute_zip_url) for the best LAME bundle on the page.
    Prefers x64 over x86 over unspecified; among those, highest version wins."""
    try:
        html = _http_get(_RAREWARES_LAME, timeout=15).decode("utf-8", errors="replace")
    except OSError:
        return None, None
    candidates: list[tuple[tuple, str, str]] = []
    for m in re.finditer(r'href=["\']([^"\']*lame[^"\']*\.zip)["\']', html, re.IGNORECASE):
        href = m.group(1)
        name = href.rsplit("/", 1)[-1].lower()
        vm = re.search(r"lame[-_]?(\d+(?:\.\d+){1,2})", name)
        if not vm:
            continue
        version = vm.group(1)
        if "x64" in name or "win64" in name:
            arch_score = 2
        elif "x86" in name or "win32" in name:
            arch_score = 1
        else:
            arch_score = 0
        url = href if href.startswith("http") else urllib.parse.urljoin(_RAREWARES_LAME, href)
        candidates.append(((arch_score,) + _vtuple(version), version, url))
    if not candidates:
        return None, None
    candidates.sort(reverse=True)
    _, v, url = candidates[0]
    return v, url


def _lame_latest() -> Optional[str]:
    v, _ = _lame_find()
    return v


def _lame_install(version: str) -> None:
    _, url = _lame_find()
    if not url:
        raise RuntimeError("could not locate LAME zip URL on rarewares.org")
    data = _http_get(url, timeout=180)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    ok = _extract_one(
        data,
        lambda m: _basename(m).lower() == "lame.exe",
        TOOLS_DIR / "lame.exe",
    )
    if not ok:
        raise RuntimeError(f"lame.exe not found in {url}")


# ============================================================
# MediaInfo CLI  (GitHub Releases)
# ============================================================

_MEDIAINFO_API = "https://api.github.com/repos/MediaArea/MediaInfo/releases/latest"


def _mediainfo_release() -> dict:
    return json.loads(_http_get(_MEDIAINFO_API, timeout=15))


def _mediainfo_latest() -> Optional[str]:
    try:
        j = _mediainfo_release()
    except OSError:
        return None
    tag = j.get("tag_name") or j.get("name") or ""
    m = re.search(r"(\d+(?:\.\d+){1,2})", tag)
    return m.group(1) if m else None


def _mediainfo_install(version: str) -> None:
    # MediaInfo's GitHub Releases carry no binaries; mediaarea.net hosts the
    # actual zips under a stable URL pattern keyed off the version number.
    url = (
        f"https://mediaarea.net/download/binary/mediainfo/{version}/"
        f"MediaInfo_CLI_{version}_Windows_x64.zip"
    )
    try:
        data = _http_get(url, timeout=300)
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        # Some releases use a 'YY.MM.N' pattern; try the GitHub tag verbatim too.
        if e.code == 404:
            j = _mediainfo_release()
            tag_v = (j.get("tag_name") or "").lstrip("v")
            if tag_v and tag_v != version:
                url = (
                    f"https://mediaarea.net/download/binary/mediainfo/{tag_v}/"
                    f"MediaInfo_CLI_{tag_v}_Windows_x64.zip"
                )
                data = _http_get(url, timeout=300)
            else:
                raise
        else:
            raise
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    ok = _extract_one(
        data,
        lambda m: _basename(m).lower() == "mediainfo.exe",
        TOOLS_DIR / "MediaInfo.exe",
    )
    if not ok:
        raise RuntimeError(f"MediaInfo.exe not found inside {url}")


# ============================================================
# SoX  (SourceForge — frozen at 14.4.2 since 2015)
# ============================================================

_SOX_VERSION = "14.4.2"


def _sox_latest() -> Optional[str]:
    return _SOX_VERSION


def _sox_install(version: str) -> None:
    url = (
        f"https://sourceforge.net/projects/sox/files/sox/{version}/"
        f"sox-{version}-win32.zip/download"
    )
    data = _http_get(url, timeout=300)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        sox_dir: Optional[str] = None
        for m in zf.namelist():
            if _basename(m).lower() == "sox.exe":
                head = m.replace("\\", "/").rsplit("/", 1)
                sox_dir = head[0] if len(head) == 2 else ""
                break
        if sox_dir is None:
            raise RuntimeError("sox.exe not found inside SourceForge zip")
        prefix = (sox_dir + "/") if sox_dir else ""
        for m in zf.namelist():
            norm = m.replace("\\", "/")
            if prefix and not norm.startswith(prefix):
                continue
            if norm.endswith("/"):
                continue
            rel = norm[len(prefix):] if prefix else norm
            if not rel:
                continue
            target = TOOLS_DIR / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


# ============================================================
# FFmpeg + ffprobe  (BtbN GitHub Releases — required for DSD support and merge)
# ============================================================

_FFMPEG_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"


def _ffmpeg_release() -> dict:
    return json.loads(_http_get(_FFMPEG_API, timeout=15))


def _ffmpeg_latest() -> Optional[str]:
    try:
        j = _ffmpeg_release()
    except OSError:
        return None
    # BtbN uses a rolling 'latest' tag; surface the publish date for a meaningful label.
    published = j.get("published_at") or ""
    if published:
        return published.split("T", 1)[0]  # YYYY-MM-DD
    return j.get("tag_name") or j.get("name") or None


def _ffmpeg_install(version: str) -> None:
    j = _ffmpeg_release()
    # Prefer the rolling master GPL win64 build — it is always present and tracks HEAD.
    preferred = "ffmpeg-master-latest-win64-gpl.zip"
    asset = next((a for a in j.get("assets", []) if a.get("name", "") == preferred), None)
    if not asset:
        # Fallback: any *win64-gpl.zip that is not a -shared variant
        for a in j.get("assets", []):
            n = a.get("name", "")
            if re.search(r"win64-gpl(?!-shared).*\.zip$", n, re.IGNORECASE):
                asset = a
                break
    if not asset:
        raise RuntimeError("no win64 GPL ffmpeg asset in latest BtbN release")
    data = _http_get(asset["browser_download_url"], timeout=900)  # ~75-110 MB
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    extracted = {"ffmpeg.exe": False, "ffprobe.exe": False}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for m in zf.namelist():
            base = _basename(m).lower()
            if base in extracted and not extracted[base]:
                with zf.open(m) as src, open(TOOLS_DIR / base, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted[base] = True
    if not extracted["ffmpeg.exe"]:
        raise RuntimeError("ffmpeg.exe not found in BtbN zip")


# ============================================================
# Spek  (GitHub Releases — MSI downloaded and extracted via msiexec /a)
# ============================================================

_SPEK_API = "https://api.github.com/repos/alexkay/spek/releases/latest"


def _spek_latest() -> Optional[str]:
    try:
        j = json.loads(_http_get(_SPEK_API, timeout=15))
    except OSError:
        return None
    tag = j.get("tag_name") or j.get("name") or ""
    m = re.search(r"(\d+(?:\.\d+){1,2})", tag)
    return m.group(1) if m else None


def _spek_install(version: str) -> None:
    import tempfile
    j = json.loads(_http_get(_SPEK_API, timeout=15))
    asset = next(
        (a for a in j.get("assets", [])
         if "windows" in a.get("name", "").lower() and a["name"].endswith(".msi")),
        None,
    )
    if not asset:
        raise RuntimeError(f"No Windows MSI asset found in Spek {version} release")
    msi_data = _http_get(asset["browser_download_url"], timeout=300)
    msi_path = Path(tempfile.gettempdir()) / f"spek-{version}-windows.msi"
    msi_path.write_bytes(msi_data)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    extract_dir = TOOLS_DIR / "_spek_extract"
    # /a = administrative install (extracts files without registry changes)
    subprocess.run(
        ["msiexec", "/a", str(msi_path), "/qn", f"TARGETDIR={extract_dir}"],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    spek_exes = list(extract_dir.rglob("spek.exe")) if extract_dir.exists() else []
    if not spek_exes:
        msi_path.unlink(missing_ok=True)
        raise RuntimeError("spek.exe not found after MSI extraction")
    shutil.copy2(str(spek_exes[0]), TOOLS_DIR / "spek.exe")
    msi_path.unlink(missing_ok=True)
    shutil.rmtree(str(extract_dir), ignore_errors=True)


# ============================================================
# xrecode3  (xrecode.com — scrapes download page for zip URL)
# ============================================================

_XRECODE_HOME = "https://xrecode.com/"


def _xrecode_find() -> tuple[Optional[str], Optional[str]]:
    """Return (version, zip_url) by scraping the xrecode homepage."""
    try:
        html = _http_get(_XRECODE_HOME, timeout=15).decode("utf-8", errors="replace")
    except OSError:
        return None, None
    for m in re.finditer(r'href=["\']([^"\']*xrecode[^"\']*\.zip)["\']', html, re.IGNORECASE):
        url = m.group(1)
        if not url.startswith("http"):
            url = urllib.parse.urljoin(_XRECODE_HOME, url)
        vm = re.search(r"(\d+\.\d+(?:\.\d+)?)", url)
        return (vm.group(1) if vm else "?"), url
    return None, None


def _xrecode_latest() -> Optional[str]:
    v, _ = _xrecode_find()
    return v


def _xrecode_install(version: str) -> None:
    _, url = _xrecode_find()
    if not url:
        raise RuntimeError("Could not find xrecode3 download URL on xrecode.com")
    data = _http_get(url, timeout=300)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    ok = _extract_one(data, lambda m: _basename(m).lower() == "xrecode3.exe",
                      TOOLS_DIR / "xrecode3.exe")
    if not ok:
        raise RuntimeError(f"xrecode3.exe not found inside {url}")


# ============================================================
# Bulk Rename Utility  (bulkrenameutility.co.uk — portable free zip)
# ============================================================

_BRU_HOME = "https://www.bulkrenameutility.co.uk/Download.php"


def _bru_find() -> tuple[Optional[str], Optional[str]]:
    """Return (version, zip_url) by scraping the BRU download page."""
    try:
        html = _http_get(_BRU_HOME, timeout=15).decode("utf-8", errors="replace")
    except OSError:
        return None, None
    for m in re.finditer(r'href=["\']([^"\']*BRU[^"\']*\.zip)["\']', html, re.IGNORECASE):
        url = m.group(1)
        if not url.startswith("http"):
            url = urllib.parse.urljoin(_BRU_HOME, url)
        vm = re.search(r"(\d+\.\d+(?:\.\d+)?)", url)
        return (vm.group(1) if vm else "?"), url
    return None, None


def _bru_latest() -> Optional[str]:
    v, _ = _bru_find()
    return v


def _bru_install(version: str) -> None:
    _, url = _bru_find()
    if not url:
        raise RuntimeError("Could not find BRU portable zip on bulkrenameutility.co.uk")
    data = _http_get(url, timeout=300)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    ok = _extract_one(
        data,
        lambda m: _basename(m).lower() == "bulk rename utility.exe",
        TOOLS_DIR / "Bulk Rename Utility.exe",
    )
    if not ok:
        raise RuntimeError(f"'Bulk Rename Utility.exe' not found inside {url}")


# ============================================================
# Tool registry
# ============================================================

TOOLS: list[Tool] = [
    Tool(
        name="flac",
        exe="flac.exe",
        description="FLAC reference encoder/decoder",
        homepage="https://xiph.org/flac/",
        version_re=r"flac\s+([\d.]+)",
        upstream_check=_flac_latest,
        upstream_install=_flac_install,
    ),
    Tool(
        name="metaflac",
        exe="metaflac.exe",
        description="FLAC metadata editor (installed alongside flac)",
        homepage="https://xiph.org/flac/",
        version_re=r"metaflac\s+([\d.]+)",
        upstream_check=_flac_latest,
        upstream_install=_flac_install,
    ),
    Tool(
        name="lame",
        exe="lame.exe",
        description="LAME MP3 encoder",
        homepage="https://www.rarewares.org/mp3-lame-bundle.php",
        # "LAME 64bits version 3.100.1 (https://...)"
        version_re=r"LAME(?:\s+\d+\s*bits?)?\s+(?:version\s+)?(\d+\.\d+(?:\.\d+)?)",
        upstream_check=_lame_latest,
        upstream_install=_lame_install,
    ),
    Tool(
        name="mediainfo",
        exe="MediaInfo.exe",
        description="MediaInfo CLI - detailed audio file inspection",
        homepage="https://mediaarea.net/en/MediaInfo",
        version_re=r"MediaInfo[^\d]*v?([\d.]+)",
        upstream_check=_mediainfo_latest,
        upstream_install=_mediainfo_install,
    ),
    Tool(
        name="ffmpeg",
        exe="ffmpeg.exe",
        description="FFmpeg - DSD/merge/conversion engine",
        homepage="https://ffmpeg.org/",
        version_re=r"ffmpeg\s+version\s+(\S+)",
        upstream_check=_ffmpeg_latest,
        upstream_install=_ffmpeg_install,
    ),
    Tool(
        name="ffprobe",
        exe="ffprobe.exe",
        description="FFprobe - audio file inspection (installed alongside ffmpeg)",
        homepage="https://ffmpeg.org/",
        version_re=r"ffprobe\s+version\s+(\S+)",
        upstream_check=_ffmpeg_latest,
        upstream_install=_ffmpeg_install,
    ),
    Tool(
        name="sox",
        exe="sox.exe",
        description="SoX - Swiss-army audio knife (frozen at 14.4.2)",
        homepage="https://sourceforge.net/projects/sox/",
        version_re=r"SoX\s+v?([\d.]+)",
        upstream_check=_sox_latest,
        upstream_install=_sox_install,
    ),
    Tool(
        name="spek",
        exe="spek.exe",
        description="Spek - acoustic spectrum analyser",
        homepage="https://www.spek.cc/p/download",
        upstream_check=_spek_latest,
        upstream_install=_spek_install,
        gui_only=True,
        system_paths=[
            r"C:\Program Files\Spek\spek.exe",
            r"C:\Program Files (x86)\Spek\spek.exe",
        ],
    ),
    Tool(
        name="shorten",
        exe="shorten.exe",
        description="Shorten (.shn) decoder — abandoned upstream, no updates",
        homepage="http://etree.org/shnutils/",
        legacy=True,
    ),
    Tool(
        name="aucdtect",
        exe="aucdtect.exe",
        description="auCDtect — detect lossy-in-lossless — abandoned upstream, no updates",
        homepage="http://retired.true-audio.com/",
        legacy=True,
    ),
    Tool(
        name="xrecode3",
        exe="xrecode3.exe",
        description="xrecode3 - audio/DSD batch converter (free trial; full version $10)",
        homepage="https://xrecode.com/",
        version_args=["/version"],
        version_re=r"xrecode\s*3?\s*[v]?([\d.]+)",
        upstream_check=_xrecode_latest,
        upstream_install=_xrecode_install,
        gui_only=True,
        system_paths=[
            r"C:\Program Files\xrecode3\xrecode3.exe",
            r"C:\Program Files (x86)\xrecode3\xrecode3.exe",
        ],
    ),
    Tool(
        name="bru",
        exe="Bulk Rename Utility.exe",
        description="Bulk Rename Utility - powerful free file renamer",
        homepage="https://www.bulkrenameutility.co.uk/Download.php",
        upstream_check=_bru_latest,
        upstream_install=_bru_install,
        gui_only=True,
        system_paths=[
            r"C:\Program Files\Bulk Rename Utility\Bulk Rename Utility.exe",
            r"C:\Program Files (x86)\Bulk Rename Utility\Bulk Rename Utility.exe",
        ],
    ),
    Tool(
        name="tascam_hireseditor",
        exe="hiResEditor.exe",
        description="TASCAM Hi-Res Editor - DSD/DFF/DSF editor and combiner (GUI)",
        homepage="https://tascam.com/us/product/hi-res_editor/top",
        gui_only=True,
        system_paths=[
            r"C:\Program Files\TASCAM\HiResEditor\hiResEditor.exe",
            r"C:\Program Files (x86)\TASCAM\HiResEditor\hiResEditor.exe",
        ],
    ),
    Tool(
        name="korg_audiogate",
        exe="AudioGate.exe",
        description="KORG AudioGate - DSD player/converter (GUI)",
        homepage="https://www.korg.com/us/products/software/audiogate4/",
        gui_only=True,
        system_paths=[
            r"C:\Program Files (x86)\KORG\AudioGate 4.6\AudioGate.exe",
            r"C:\Program Files\KORG\AudioGate 4.6\AudioGate.exe",
            r"C:\Program Files (x86)\KORG\AudioGate 4\AudioGate.exe",
        ],
    ),
]


def get_tool(name: str) -> Tool:
    for t in TOOLS:
        if t.name == name:
            return t
    raise KeyError(name)
