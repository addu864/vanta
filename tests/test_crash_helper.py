import json
import zipfile

from vanta.launcher.minecraft import monitor

MIXIN = """---- Minecraft Crash Report ----
Description: Initializing game
org.spongepowered.asm.mixin.transformer.throwables.MixinTransformerError: An unexpected critical error was encountered
Caused by: org.spongepowered.asm.mixin.throwables.MixinApplyError: Mixin [sodium.mixins.json:core.MinecraftMixin] from mod sodium failed injection check
Mixin apply for mod sodium failed sodium.mixins.json:core.MinecraftMixin from mod sodium
"""
FABRIC = """[main/ERROR]: Incompatible mods found!
 - Mod 'Iris' (iris) 1.8.8 requires version 0.6.0 or later of mod 'Sodium' (sodium), but only the wrong version is present: 0.5.11!
"""


def test_suspects_from_mixin_and_fabric():
    assert monitor.suspect_mods(MIXIN) == ["sodium"]
    s = monitor.summarize_crash(FABRIC.splitlines())
    assert s["suspects"][:2] == ["iris", "sodium"]
    assert monitor.suspect_mods("Suspected Mods: Lithium (lithium), Minecraft (minecraft)") == ["lithium"]
    assert monitor.suspect_mods("nothing here") == []


def test_mod_files_for_reads_fabric_mod_json(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    with zipfile.ZipFile(mods / "weird-name-1.0.jar", "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps({"id": "sodium"}))
    with zipfile.ZipFile(mods / "iris-1.8.8.jar", "w") as zf:
        zf.writestr("other.txt", "x")
    out = monitor.mod_files_for(tmp_path, ["sodium", "iris", "ghost"])
    assert {"id": "sodium", "file": "weird-name-1.0.jar"} in out and {"id": "iris", "file": "iris-1.8.8.jar"} in out
    assert len(out) == 2


def test_crash_disable_only_suspects(tmp_path, monkeypatch):
    from vanta.launcher.ui.app import VantaApp
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    app = VantaApp()
    game = tmp_path / "g"
    (game / "1.21.1-fabric" / "mods").mkdir(parents=True)
    (game / "1.21.1-fabric" / "mods" / "bad.jar").write_bytes(b"x")
    (game / "1.21.1-fabric" / "mods" / "good.jar").write_bytes(b"x")
    monkeypatch.setattr(app, "_game_root", lambda: game)
    monkeypatch.setattr(app, "_game_running", lambda: False)
    monitor._state["crash"] = {"instance": "1.21.1-fabric", "suspectFiles": [{"id": "bad", "file": "bad.jar"}]}
    try:
        assert not app.crash_disable({"file": "good.jar"})["ok"]
        assert app.crash_disable({"file": "bad.jar"})["ok"]
        assert (game / "1.21.1-fabric" / "mods" / "bad.jar.disabled").is_file()
        monitor._state["crash"] = {"instance": "1.21.11-fabric", "suspectFiles": [{"id": "x", "file": "x.jar"}]}
        assert "main instance" in app.crash_disable({"file": "x.jar"})["error"]
    finally:
        monitor._state["crash"] = None
