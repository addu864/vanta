import json
from pathlib import Path

from vanta.launcher.minecraft import instances, monitor
from vanta.launcher.updates import github


def test_presets_and_mod_lists():
    assert set(instances.PRESETS) == {"vanilla", "fast", "custom", "trailer"}
    assert instances.preset_mod_list("vanilla") == []
    fast = [s for s, _ in instances.preset_mod_list("fast")]
    for slug in ("fabric-api", "sodium", "lithium", "modernfix", "immediatelyfast", "entityculling", "modmenu"):
        assert slug in fast
    trailer = [s for s, _ in instances.preset_mod_list("trailer")]
    assert set(fast) <= set(trailer) and {"iris", "gamma-utils", "visuality", "not-enough-animations"} <= set(trailer)
    custom = [s for s, _ in instances.preset_mod_list("custom", ["journeymap", "sodium"], include_fast=False)]
    assert custom == ["fabric-api", "journeymap", "sodium"]
    custom_fast = [s for s, _ in instances.preset_mod_list("custom", ["journeymap"], include_fast=True)]
    assert custom_fast[: len(fast)] == fast and custom_fast[-1] == "journeymap"


def test_start_install_uses_preset(tmp_path):
    calls = {}

    def fake_installer(root, **kw):
        calls["loader"] = kw["loader"]
        (Path(root) / kw["instance_name"]).mkdir(parents=True, exist_ok=True)
        return {"javaMajor": 17, "assetsRoot": str(root / "assets")}

    res = instances.start_install(tmp_path, "1.20.1", "fabric", installer=fake_installer, preset="vanilla",
                                  loader_version=lambda: "0.1", background=False)
    assert res["job"]["state"] == "done" and calls["loader"] is None
    meta = json.loads((tmp_path / "1.20.1-vanilla" / instances.META_NAME).read_text())
    assert meta["preset"] == "vanilla" and meta["trailer"] is False


def test_trailer_toggle_on_and_off(tmp_path):
    inst = tmp_path / "1.20.1-fabric"
    (inst / "mods").mkdir(parents=True)
    (inst / "mods" / "iris-1.7.6.jar").write_bytes(b"x")
    (inst / "mods" / "sodium.jar").write_bytes(b"x")
    (inst / "resourcepacks").mkdir()
    (inst / "resourcepacks" / "vanta-visual-set.json").write_text(json.dumps({"placed": {"bb": {"kind": "resourcepack", "filename": "BB.zip"}}}))
    (inst / instances.META_NAME).write_text("{}")

    def fake_visual(instance, game_version):
        from vanta.launcher.mods.visual_set import enable_resource_packs
        enable_resource_packs(Path(instance) / "options.txt", ["BB.zip"])
        return {"skipped": [{"title": "Fresh Animations", "reason": "No 1.20.1 build."}]}

    on = instances.set_trailer(inst, "1.20.1", True, mods_runner=lambda *a, **k: {"skipped": []}, visual_installer=fake_visual)
    opts = (inst / "options.txt").read_text()
    assert on["ok"] and "key.keyboard.b" in opts and "toggleUse:false" in opts and "file/BB.zip" in opts
    assert on["skipped"] == ["Fresh Animations: No 1.20.1 build."]
    off = instances.set_trailer(inst, "1.20.1", False)
    assert off["ok"] and (inst / "mods" / "iris-1.7.6.jar.disabled").is_file() and (inst / "mods" / "sodium.jar").is_file()
    assert "file/BB.zip" not in (inst / "options.txt").read_text()
    assert json.loads((inst / instances.META_NAME).read_text())["trailer"] is False
    instances.set_trailer(inst, "1.20.1", True, mods_runner=lambda *a, **k: {"skipped": []}, visual_installer=fake_visual)
    assert (inst / "mods" / "iris-1.7.6.jar").is_file()


def test_trailer_refuses_main_and_vanilla(tmp_path):
    assert not instances.set_trailer(tmp_path / "1.21.11-fabric", "1.21.11", False)["ok"]
    assert not instances.set_trailer(tmp_path / "1.20.1-vanilla", "1.20.1", True)["ok"]


def test_update_check_and_plan(tmp_path):
    rels = [{"tag_name": "v1.2.0"}, {"tag_name": "v1.2.0-mac"}, {"tag_name": "v9.9.0", "draft": True}, {"tag_name": "v1.10.0"}]
    assert github.latest_release(rels)["tag_name"] == "v1.10.0"
    info = github.check_for_updates(fetch=lambda url: rels, current="1.3.0", force=True)
    assert info["available"] and info["latest"] == "v1.10.0"
    (tmp_path / "vanta").mkdir()
    (tmp_path / "vanta" / "same.py").write_bytes(b"a")
    tree = [{"path": "vanta/same.py", "type": "blob", "sha": github.git_blob_sha(b"a")},
            {"path": "vanta/new.py", "type": "blob", "sha": github.git_blob_sha(b"new")},
            {"path": ".vanta-data/game/x.jar", "type": "blob", "sha": "0"},
            {"path": "vanta/../evil.py", "type": "blob", "sha": "0"}]
    assert [t["path"] for t in github.plan_update(tmp_path, tree)] == ["vanta/new.py"]
    res = github.apply_update(tmp_path, "v1.10.0", fetch_json=lambda u: {"tree": tree}, fetch_bytes=lambda u: b"new")
    assert res["ok"] and (tmp_path / "vanta" / "new.py").read_bytes() == b"new"
    bad = github.apply_update(tmp_path, "v1", fetch_json=lambda u: {"tree": [{"path": "vanta/x.py", "type": "blob", "sha": "f" * 40}]},
                              fetch_bytes=lambda u: b"tampered")
    assert not bad["ok"] and not (tmp_path / "vanta" / "x.py").exists()


def test_crash_summary_hints():
    s = monitor.summarize_crash([], "---- Minecraft Crash Report ----\nDescription: Initializing game\n\njava.lang.OutOfMemoryError: Java heap space\n")
    assert s["title"] == "Initializing game" and "memory" in s["hint"] and "OutOfMemoryError" in s["exception"]
    s2 = monitor.summarize_crash(["[main/ERROR]: Incompatible mods found!", "net.fabricmc.loader.impl.FormattedException: Mod resolution failed"])
    assert "dependency" in s2["hint"]


def test_instance_jvm_args(tmp_path):
    from vanta.launcher.minecraft.launch import instance_jvm_args
    (tmp_path / "vanta-settings.json").write_text(json.dumps({"jvmArgs": "-XX:+UseG1GC -Dx=1 --evil rm"}))
    assert instance_jvm_args(tmp_path) == ["-XX:+UseG1GC", "-Dx=1"]
