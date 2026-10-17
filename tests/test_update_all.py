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
