"""Tests for CLI tool path resolution (the .exe-on-Mac bug)."""
import sys

from audiohelper.tools import Tool


class _FakeConfig:
    def __init__(self, overrides=None):
        self._d = {"tool_paths": overrides or {}}

    def get(self, key, default=None):
        return self._d.get(key, default)


def test_override_wins(tmp_path):
    custom = tmp_path / "myffmpeg"
    custom.write_bytes(b"")
    t = Tool(name="ffmpeg", exe="ffmpeg.exe", description="")
    cfg = _FakeConfig({"ffmpeg": str(custom)})
    assert t.path(cfg) == custom


def test_strips_exe_on_non_windows(monkeypatch):
    """On non-Windows, a bundled .exe must be ignored and PATH searched
    with the suffix stripped so /opt/homebrew/bin/ffmpeg is found."""
    if sys.platform == "win32":
        import pytest
        pytest.skip("non-Windows behavior")
    t = Tool(name="ffmpeg", exe="ffmpeg.exe", description="")
    p = t.path(_FakeConfig())
    # Should resolve to a real on-PATH ffmpeg (no .exe), or a non-existent
    # bundled fallback — but never a Windows .exe that exists.
    assert not (p.suffix == ".exe" and p.exists())


def test_resolves_system_ffmpeg(monkeypatch):
    if sys.platform == "win32":
        import pytest
        pytest.skip("non-Windows behavior")
    import shutil
    if not shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg not on PATH")
    t = Tool(name="ffmpeg", exe="ffmpeg.exe", description="")
    p = t.path(_FakeConfig())
    assert p.exists()
    assert p.name in ("ffmpeg", "ffmpeg.exe")
