from pathlib import Path

from vanta.launcher.mods import browser as B


def test_update_all_installs_each_update_and_survives_failures(tmp_path, monkeypatch):
    b = B.ModBrowser(tmp_path / "cache", opener=lambda *a, **k: None)
    index = {"aaa": {"title": "Alpha", "update": {"versionId": "v2", "number": "2.0"}},
             "bbb": {"title": "Beta", "update": {"versionId": "w9", "number": "9"}},
             "ccc": {"title": "Same"}}
    monkeypatch.setattr(b, "installed", lambda *a, **k: index)
    calls = []

    def install(inst, pid, game, loader, vid, progress=None):
        calls.append((pid, vid))
        if pid == "bbb":
            raise B.BrowserError("sha512 mismatch")
        return {}
    monkeypatch.setattr(b, "install", install)
    steps = []
    r = b.update_all(Path(tmp_path), "1.21.1", "fabric", progress=lambda s, d, t: steps.append(s))
    assert calls == [("aaa", "v2"), ("bbb", "w9")]
    assert r["updated"] == [{"title": "Alpha", "number": "2.0"}]
    assert r["failed"][0]["title"] == "Beta" and r["checked"] == 3
    assert steps[-1] == "Done"


def test_ui_has_update_all():
    html = (Path(__file__).resolve().parents[1] / "vanta/launcher/ui/index.html").read_text(encoding="utf-8")
    assert 'id="mb-update-all"' in html and "/api/browse/updateall" in html


def test_download_retries_then_succeeds(tmp_path):
    import hashlib
    import io
    import urllib.error

    good = b"JAR-BYTES"
    calls = []

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=0):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.URLError("reset")
        if len(calls) == 2:
            return R(good[:3])  # cut off
        return R(good)
    b = B.ModBrowser(tmp_path, opener=opener)
    b.retry_delay = 0
    assert b._download("https://cdn.modrinth.com/x.jar", {"sha1": hashlib.sha1(good).hexdigest()}) == good
    assert len(calls) == 3
    calls.clear()
    b.download_attempts = 1
    import pytest
    with pytest.raises(B.BrowserError):
        b._download("https://cdn.modrinth.com/x.jar", {})
