"""Downloadable instances 1.18..26.3, Java selection, old natives."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from vanta.launcher.minecraft import install, instances
from vanta.launcher.minecraft.versions import list_versions
from vanta.shared.java import select_java

MANIFEST = {"versions": [
    {"id": "26.4-snapshot-3", "type": "snapshot"},
    {"id": "26.3", "type": "release", "releaseTime": "2026-09-01T00:00:00Z"},
    {"id": "26.1.2", "type": "release"},
    {"id": "1.21.11", "type": "release"},
    {"id": "1.20.1", "type": "release"},
    {"id": "1.18", "type": "release"},
    {"id": "1.17.1", "type": "release"},
    {"id": "1.16.5", "type": "release"},
]}


def test_only_real_releases_from_1_18_to_26_3() -> None:
    rows = instances.filter_releases(MANIFEST, [{"version": "1.20.1"}, {"version": "26.3"}])
    assert [r["id"] for r in rows] == ["26.3", "26.1.2", "1.21.11", "1.20.1", "1.18"]
    assert {r["id"]: r["fabric"] for r in rows}["1.18"] is False
    assert rows[0]["releaseTime"] == "2026-09-01"


def test_main_instance_is_never_redownloaded(tmp_path: Path) -> None:
    result = instances.start_install(tmp_path, "1.21.11", "fabric", background=False)
    assert result["ok"] is False and "1.21.11-fabric" in result["error"]
    assert instances.start_install(tmp_path, "1.16.5", "fabric", background=False)["ok"] is False


def test_install_job_writes_instance_and_lists_it(tmp_path: Path) -> None:
    calls = {}

    def fake_installer(root, **kw):
        calls.update(kw)
        inst = root / kw["instance_name"]
        inst.mkdir(parents=True)
        (inst / "client.jar").write_bytes(b"jar")
        kw["progress"]("assets", 5, 10)
        return {"javaMajor": 17, "assetsRoot": str(root / "assets")}

    def fake_mods(mods_dir, version):
        return {"installed": [{"title": "Sodium"}], "skipped": [{"title": "Lithium", "reason": "No 1.20.1 fabric build."}]}

    out = instances.start_install(tmp_path, "1.20.1", "fabric", installer=fake_installer, mods_installer=fake_mods, loader_version=lambda: "0.19.5", background=False)
    assert out["ok"]
    job = instances.job("1.20.1-fabric")
    assert job["state"] == "done" and job["percent"] == 100
    assert calls["loader"] == "0.19.5" and calls["assets_root"] == tmp_path / "assets" and calls["default_mods"] is False
    meta = json.loads((tmp_path / "1.20.1-fabric" / "vanta-instance.json").read_text())
    assert meta["javaMajor"] == 17 and meta["skippedMods"] == ["Lithium: No 1.20.1 fabric build."]
    rows = list_versions(str(tmp_path))
    entry = next(v for v in rows if v["versionNumber"] == "1.20.1")
    assert entry["supported"] is True and entry["downloaded"] is True and entry["loader"] == "fabric"
    assert any(v["versionNumber"] == "1.21.11" for v in rows)


def test_vanilla_install_skips_fabric_and_mods(tmp_path: Path) -> None:
    seen = {}

    def fake_installer(root, **kw):
        seen.update(kw)
        inst = root / kw["instance_name"]
        inst.mkdir(parents=True)
        (inst / "client.jar").write_bytes(b"jar")
        return {"javaMajor": 21}

    instances.start_install(tmp_path, "1.21.4", "vanilla", installer=fake_installer, mods_installer=lambda *a: (_ for _ in ()).throw(AssertionError("no mods")), background=False)
    assert seen["loader"] is None and seen["instance_name"] == "1.21.4-vanilla"


def test_select_java_prefers_closest_suitable_major() -> None:
    javas = [{"path": "/j25", "major": 25}, {"path": "/j17", "major": 17}, {"path": "/j21", "major": 21}, {"path": "/j8", "major": 8}]
    assert select_java(17, javas) == ("/j17", "")
    assert select_java(21, javas) == ("/j21", "")
    assert select_java(25, javas) == ("/j25", "")
    path, error = select_java(25, [{"path": "/j21", "major": 21}])
    assert path is None and "Java 25" in error and "Java 21" in error


def test_old_style_natives_are_picked_and_extracted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(install.platform, "system", lambda: "Windows")
    lib = {"name": "org.lwjgl:lwjgl:3.2.1", "natives": {"windows": "natives-windows", "osx": "natives-macos"},
           "downloads": {"classifiers": {"natives-windows": {"url": "https://x/w.jar", "path": "w.jar"}, "natives-macos": {"url": "https://x/m.jar", "path": "m.jar"}}}}
    assert install.native_classifier(lib)["path"] == "w.jar"
    monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    assert install.native_classifier(lib)["path"] == "m.jar"
    jar = tmp_path / "n.jar"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lwjgl.dll", b"dll")
        z.writestr("META-INF/MANIFEST.MF", b"x")
        z.writestr("../evil.dll", b"x")
    jar.write_bytes(buf.getvalue())
    install._extract_natives(jar, tmp_path / "natives", ["META-INF/"])
    assert (tmp_path / "natives" / "lwjgl.dll").read_bytes() == b"dll"
    assert not (tmp_path / "natives" / "META-INF").exists()
    assert not (tmp_path / "evil.dll").exists()


def test_required_java_read_from_mojang_json(tmp_path: Path) -> None:
    from vanta.launcher.minecraft.launch import required_java

    (tmp_path / "1.20.1.json").write_text(json.dumps({"javaVersion": {"majorVersion": 17}}))
    assert required_java(tmp_path, "1.20.1") == 17
    assert required_java(tmp_path, "9.9") == 8
