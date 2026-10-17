from pathlib import Path

from vanta.launcher.mods.browser import BrowserError, ModBrowser

HTML = (Path(__file__).resolve().parents[1] / "vanta/launcher/ui/index.html").read_text(encoding="utf-8")


def test_copy_from_installs_for_target_version_and_skips_missing(tmp_path, monkeypatch):
    b = ModBrowser(tmp_path / "cache")
    src, dst = tmp_path / "a", tmp_path / "b"
    src.mkdir(); dst.mkdir()
    indexes = {
        src: {"sodium": {"title": "Sodium", "file": "sodium.jar"}, "iris": {"title": "Iris", "file": "iris.jar"},
              "old": {"title": "Old", "file": "old.jar.disabled"}, "lith": {"title": "Lithium", "file": "lith.jar"}},
        dst: {"lith": {"title": "Lithium", "file": "lith2.jar"}},
    }
    monkeypatch.setattr(b, "installed", lambda inst, *a, **k: indexes[inst])
    calls = []

    def install(inst, pid, game, loader="fabric", version_id="", progress=None):
        calls.append((inst, pid, game))
        if pid == "iris":
            raise BrowserError("No version for 1.21.4.")
        return {"installed": [{"title": "Sodium"}]}
    monkeypatch.setattr(b, "install", install)
    r = b.copy_from(src, "1.21.1", "fabric", dst, "1.21.4", "fabric")
    assert calls == [(dst, "sodium", "1.21.4"), (dst, "iris", "1.21.4")]  # disabled and already-present are skipped
    assert r["copied"] == ["Sodium"] and r["skipped"][0]["title"] == "Iris" and r["already"] == 2


def test_copy_mods_validation(tmp_path, monkeypatch):
    from vanta.launcher.ui.app import VantaApp
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    app = VantaApp()
    assert app.copy_instance_mods({})["ok"] is False
    folder = tmp_path / "x"; folder.mkdir()
    monkeypatch.setattr(app, "_browse_ctx", lambda body: (folder, "1.21.1", "fabric"))
    monkeypatch.setattr(app, "_instance_dir", lambda body: (folder, {"loader": "fabric"}))
    r = app.copy_instance_mods({"from": {"versionNumber": "1.21.1", "loader": "fabric"}, "to": {"versionNumber": "1.21.1", "loader": "fabric"}})
    assert r["ok"] is False and "different" in r["error"]
    for needle in ('id="inst-copy-to"', 'id="inst-copy"', "/api/instances/copy-mods"):
        assert needle in HTML
