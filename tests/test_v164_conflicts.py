import io
import json
import zipfile

from vanta.launcher.mods import conflicts


def jar(folder, name, meta=None, extra=None, nested=None):
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as zf:
        if meta is not None:
            if nested:
                meta = dict(meta, jars=[{"file": f"META-INF/jars/{n}.jar"} for n in nested])
                for n in nested:
                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w") as inner:
                        inner.writestr("fabric.mod.json", json.dumps({"id": n, "version": "1.0.0"}))
                    zf.writestr(f"META-INF/jars/{n}.jar", buf.getvalue())
            zf.writestr("fabric.mod.json", json.dumps(meta))
        for k, v in (extra or {}).items():
            zf.writestr(k, v)


def titles(res):
    return [w["title"] for w in res["warnings"]]


def test_clean_set_has_no_warnings(tmp_path):
    m = tmp_path / "mods"
    jar(m, "fabric-api.jar", {"id": "fabric-api", "version": "0.116.0+1.21.1", "depends": {"minecraft": ">=1.21- <1.21.2-"}},
        nested=["fabric-rendering-v1", "fabric-api-base"])
    jar(m, "sodium.jar", {"id": "sodium", "version": "0.6.0+mc1.21.1", "depends": {"minecraft": ["1.21", "1.21.1"], "fabric-rendering-v1": "*"}})
    jar(m, "iris.jar", {"id": "iris", "version": "1.8.8", "depends": {"sodium": "0.6.x", "minecraft": "~1.21"}})
    assert conflicts.check(m, "1.21.1")["warnings"] == []


def test_duplicates_forge_broken_missing(tmp_path):
    m = tmp_path / "mods"
    jar(m, "sodium-a.jar", {"id": "sodium", "version": "0.5.11"})
    jar(m, "sodium-b.jar", {"id": "sodium", "version": "0.6.0"})
    jar(m, "jei-forge.jar", extra={"META-INF/mods.toml": 'modId="jei"'})
    (m / "half.jar").write_bytes(b"PK\x03\x04broken")
    jar(m, "modmenu.jar", {"id": "modmenu", "name": "Mod Menu", "version": "11", "depends": {"fabric-screen-api-v1": "*"}})
    jar(m, "needs.jar", {"id": "needs", "name": "Needy", "version": "1", "depends": {"cloth-config": "*"}})
    t = titles(conflicts.check(m, "1.21.1"))
    assert "sodium is installed 2 times" in t
    assert "Forge mods in a Fabric instance" in t
    assert "Damaged mod files" in t
    assert "Missing Fabric API" in t and "Missing cloth-config" in t


def test_versions_and_clashes(tmp_path):
    m = tmp_path / "mods"
    jar(m, "sodium.jar", {"id": "sodium", "name": "Sodium", "version": "0.5.11+mc1.20.1", "depends": {"minecraft": "1.20.1"}})
    jar(m, "iris.jar", {"id": "iris", "name": "Iris", "version": "1.8.8", "depends": {"sodium": "0.6.x"}})
    jar(m, "optifabric.jar", {"id": "optifabric", "version": "1.14"})
    res = conflicts.check(m, "1.21.1")
    t = titles(res)
    assert "Iris needs a different Sodium" in t
    assert "Mods made for a different Minecraft version" in t
    assert "Mods that don't work together" in t
    assert all(w["level"] in {"error", "warn"} and w["detail"] for w in res["warnings"])


def test_version_ranges():
    vm = conflicts.version_matches
    assert vm(">=1.21- <1.21.2-", "1.21.1") is True
    assert vm(">=1.21.2", "1.21.1") is False
    assert vm("1.21.x", "1.21.4") is True and vm("~1.20", "1.21.1") is False
    assert vm(["1.20.1", "1.20.2"], "1.20.2") is True
    assert vm("some weird thing", "1.21.1") is None  # unknown: never warn


def test_play_asks_first_then_allows(tmp_path, monkeypatch):
    from vanta.launcher.ui import app as appmod
    a = appmod.VantaApp.__new__(appmod.VantaApp)
    a.config = a.accounts = a.profiles = None
    a.data_dir = tmp_path
    logs = []
    a.log = type("L", (), {"info": lambda self, m: logs.append(m), "error": lambda self, m: logs.append(m)})()
    m = tmp_path / "inst" / "mods"
    jar(m, "a.jar", {"id": "x", "version": "1"})
    jar(m, "b.jar", {"id": "x", "version": "2"})
    monkeypatch.setattr(a, "_instance_dir", lambda body: (tmp_path / "inst", {"loader": "fabric", "versionNumber": "1.21.1"}), raising=False)
    monkeypatch.setattr(a, "_instance_settings", lambda folder: {"ramGb": 0}, raising=False)
    monkeypatch.setattr(appmod, "attempt_play", lambda **kw: {"status": "Started", "launched": True, "pid": 1})
    first = a.play({"checkConflicts": True})
    assert first["needsConfirm"] and first["conflicts"][0]["level"] == "error"
    assert a.play({"checkConflicts": True, "conflictsOk": True})["launched"] is True
    assert a.play({})["launched"] is True  # other launch paths only log
    assert any("Mod check" in l for l in logs)


def test_ui_has_conflict_dialog():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert 'id="conflicts"' in s and "checkConflicts: true" in s and "Launch anyway" in s
