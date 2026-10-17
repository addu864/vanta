import threading
import time
from pathlib import Path

from vanta.launcher.minecraft import install as mc_install
from vanta.launcher.minecraft import instances


def _slow_installer(started: threading.Event, steps: list[int]):
    def run(root, *, version, loader, download_assets, instance_name, assets_root, progress, default_mods):
        folder = Path(root) / instance_name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "client.jar").write_bytes(b"x")
        started.set()
        for i in range(200):
            progress.gate()  # what install_client does before each file
            steps.append(i)
            time.sleep(0.01)
        return {"javaMajor": 17}
    return run


def _wait(name, states, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if instances.job(name).get("state") in states:
            return instances.job(name)
        time.sleep(0.02)
    raise AssertionError(instances.job(name))


def test_cancel_removes_only_the_new_folder(tmp_path):
    started, steps = threading.Event(), []
    (tmp_path / "assets").mkdir()
    res = instances.start_install(tmp_path, "1.20.1", "vanilla", installer=_slow_installer(started, steps), preset="vanilla")
    name = res["job"]["id"]
    assert started.wait(3)
    assert instances.control_job(name, "cancel")["ok"]
    job = _wait(name, {"cancelled"})
    assert job["removedFolder"] is True
    assert not (tmp_path / name).exists()
    assert (tmp_path / "assets").is_dir()
    assert len(steps) < 200
    assert instances.control_job(name, "pause")["ok"] is False


def test_pause_then_resume_finishes(tmp_path):
    started, steps = threading.Event(), []
    res = instances.start_install(tmp_path, "1.20.2", "vanilla", installer=_slow_installer(started, steps), preset="vanilla")
    name = res["job"]["id"]
    assert started.wait(3)
    assert instances.control_job(name, "pause")["ok"]
    _wait(name, {"paused"})
    n = len(steps)
    time.sleep(0.3)
    assert len(steps) <= n + 1
    assert instances.control_job(name, "resume")["ok"]
    job = _wait(name, {"done"}, timeout=10)
    assert job["percent"] == 100 and len(steps) == 200


def test_existing_folder_is_kept_on_cancel(tmp_path):
    started, steps = threading.Event(), []
    (tmp_path / "1.20.3-vanilla").mkdir()
    (tmp_path / "1.20.3-vanilla" / "keep.txt").write_text("mine")
    res = instances.start_install(tmp_path, "1.20.3", "vanilla", installer=_slow_installer(started, steps), preset="vanilla")
    assert started.wait(3)
    instances.control_job(res["job"]["id"], "cancel")
    job = _wait(res["job"]["id"], {"cancelled"})
    assert job["removedFolder"] is False and (tmp_path / "1.20.3-vanilla" / "keep.txt").is_file()


def test_unknown_action_and_gate_not_swallowed():
    assert instances.control_job("nope", "explode")["ok"] is False
    assert issubclass(mc_install.InstallCancelled, Exception)
    import inspect
    src = inspect.getsource(mc_install.install_client)
    assert src.index("gate()") < src.index("try:")


def test_downloads_aggregator(tmp_path, monkeypatch):
    from vanta.launcher.ui.app import VantaApp
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    app = VantaApp()
    app.browser.jobs["abc"] = {"id": "abc", "project": "sodium", "title": "Sodium", "state": "running", "step": "Downloading", "done": 1, "total": 4}
    app.browser.jobs["def"] = {"id": "def", "project": "x", "state": "error", "error": "boom", "done": 0, "total": 1}
    d = app.downloads()
    rows = {r["id"]: r for r in d["downloads"]}
    assert rows["mod:abc"]["state"] == "downloading" and rows["mod:abc"]["percent"] == 25 and rows["mod:abc"]["title"] == "Sodium"
    assert rows["mod:def"]["state"] == "failed" and rows["mod:def"]["error"] == "boom"
    assert d["downloads"][0]["state"] == "downloading"
    assert app.control_download({"id": "mod:abc", "action": "cancel"})["ok"] is False
    html = (Path(__file__).resolve().parents[1] / "vanta/launcher/ui/index.html").read_text(encoding="utf-8")
    for needle in ('id="dl-panel"', 'id="dl-pill"', "/api/downloads/control", 'id="instance-cancel"'):
        assert needle in html


def test_whats_new_refetches_when_cache_predates_this_version(tmp_path):
    import json
    from vanta.launcher.updates import whatsnew
    cache = tmp_path / "releases.json"
    cache.write_text(json.dumps({"at": 1000.0, "releases": [{"tag_name": "v1.6.0", "name": "Vanta 1.6.0", "body": "- old"}]}))
    calls = []

    def fetch(url):
        calls.append(url)
        return [{"tag_name": "v9.9.9", "name": "Vanta 9.9.9", "body": "- **New thing**: yes"}]
    r = whatsnew.whats_new(cache, fetch=fetch, current="9.9.9", now=1001.0)
    assert calls and r["releases"][0]["tag"] == "v9.9.9"
    calls.clear()
    whatsnew.whats_new(cache, fetch=fetch, current="9.9.9", now=1002.0)
    assert not calls  # now fresh


def test_popup_uses_bundled_highlights_offline(tmp_path, monkeypatch):
    import vanta
    from vanta.launcher.ui.app import VantaApp
    from vanta.launcher.updates import whatsnew
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    (tmp_path / "seen-version.json").write_text('{"version": "1.5.0"}')
    monkeypatch.setattr(whatsnew, "whats_new", lambda *a, **k: {"releases": []})
    monkeypatch.setitem(whatsnew.BUNDLED_HIGHLIGHTS, vanta.__version__, ["Shipped note"])
    r = VantaApp().whats_new_popup()
    assert r["show"] and r["highlights"] == ["Shipped note"] and r["offline"] is False
