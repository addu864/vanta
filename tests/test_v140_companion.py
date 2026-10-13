"""v1.4.0: Vanta Companion jar ships with the app and lands in 1.21.11 Fabric instances."""
from pathlib import Path

from vanta.launcher.mods import companion


def test_bundled_jar_metadata():
    jar = companion.bundled_jar()
    assert jar is not None
    info = companion.jar_info(jar)
    assert info["id"] == "vanta-companion" and info["environment"] == "client"
    assert "1.21.11" in info["depends"]["minecraft"]
    assert "${" not in info["version"]
    for soft in ("iris", "modmenu"):
        assert soft not in info.get("depends", {})


def test_installs_and_replaces(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "vanta-companion-0.9.0.jar").write_bytes(b"old")
    r = companion.ensure_companion(mods, "1.21.11")
    assert r["installed"] and r["changed"]
    names = sorted(p.name for p in mods.iterdir())
    assert names == [companion.bundled_jar().name]
    assert companion.ensure_companion(mods, "1.21.11")["changed"] is False


def test_respects_disabled_and_other_versions(tmp_path):
    mods = tmp_path / "mods"
    mods.mkdir()
    (mods / "vanta-companion-1.0.0.jar.disabled").write_bytes(b"x")
    assert companion.ensure_companion(mods, "1.21.11")["installed"] is False
    other = tmp_path / "m2"
    assert companion.ensure_companion(other, "1.20.1")["installed"] is False
    assert not other.exists()
