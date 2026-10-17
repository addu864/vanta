from types import SimpleNamespace

import json
import zipfile
from vanta.launcher.mods import conflicts


def jar(folder, name, meta):
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps(meta))


def test_turned_off_dependency_offers_turn_on(tmp_path):
    m = tmp_path / "mods"
    jar(m, "iris.jar", {"id": "iris", "version": "1.8.8", "depends": {"sodium": "*", "fabric-api": "*"}})
    jar(m, "sodium-0.6.0.jar.disabled", {"id": "sodium", "name": "Sodium", "version": "0.6.0"})
    jar(m, "sodium-0.5.0.jar.disabled", {"id": "sodium", "name": "Sodium", "version": "0.5.0"})
    jar(m, "fabric-api-x.jar.disabled", {"id": "fabric-api", "name": "Fabric API", "version": "0.116.0"})
    res = conflicts.check(m, "1.21.1")
    by = {w.get("missing"): w for w in res["warnings"]}
    assert by["sodium"]["title"] == "Sodium is turned off"
    assert by["sodium"]["fix"] == {"action": "enable", "files": ["sodium-0.6.0.jar.disabled"], "label": "Turn Sodium back on"}
    assert by["fabric-api"]["fix"]["action"] == "enable"
    assert res["count"] == 1  # disabled jars are not counted as installed


def test_fix_conflict_enable_and_disable(tmp_path):
    from vanta.launcher.ui.app import VantaApp
    m = tmp_path / "1.21.1-fabric" / "mods"
    jar(m, "sodium.jar.disabled", {"id": "sodium"})
    jar(m, "dup.jar", {"id": "dup"})
    app = VantaApp.__new__(VantaApp)
    app._instance_dir = lambda body: (tmp_path / "1.21.1-fabric", {"versionNumber": "1.21.1"})
    app._game_running = lambda: False
    app.log = SimpleNamespace(info=lambda *a, **k: None)
    on = app.fix_conflict({"action": "enable", "files": ["sodium.jar.disabled", "dup.jar"]})
    assert on["ok"] and on["enabled"] == ["sodium.jar.disabled"] and on["failed"] == ["dup.jar"]
    assert (m / "sodium.jar").is_file()
    off = app.fix_conflict({"action": "disable", "files": ["dup.jar"]})
    assert off["disabled"] == ["dup.jar"] and (m / "dup.jar.disabled").is_file()
    assert not app.fix_conflict({"action": "delete", "files": ["sodium.jar"]})["ok"]


def test_ui_handles_enable_fix():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert 'fix.action === "enable"' in s


def test_duplicate_keeps_original_over_download_copy(tmp_path):
    import os
    m = tmp_path / "mods"
    jar(m, "sodium-0.6.0.jar", {"id": "sodium", "version": "0.6.0"})
    jar(m, "sodium-0.6.0 (1).jar", {"id": "sodium", "version": "0.6.0"})
    os.utime(m / "sodium-0.6.0.jar", (1, 1))
    w = conflicts.check(m)["warnings"][0]
    assert w["fix"]["files"] == ["sodium-0.6.0 (1).jar"]
