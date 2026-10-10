"""macOS support: natives/JVM rules, Java detection, data dir, first-run download."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from vanta.launcher.minecraft import install
from vanta.shared import java as javamod
from vanta.shared.config import paths

VANILLA = Path(__file__).resolve().parents[1] / ".vanta-data/game/1.21.11-fabric/1.21.11.json"


def _mac(monkeypatch, machine: str) -> None:
    monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(install.platform, "machine", lambda: machine)


@pytest.mark.parametrize("machine", ["arm64", "x86_64"])
def test_mac_rules_pick_macos_lwjgl_natives_only(monkeypatch, machine) -> None:
    _mac(monkeypatch, machine)
    vanilla = json.loads(VANILLA.read_text(encoding="utf-8"))
    names = [lib["name"] for lib in vanilla["libraries"] if install._rules_allow(lib.get("rules"))]
    natives = [n for n in names if n.startswith("org.lwjgl:lwjgl:") and "natives" in n]
    assert sorted(natives) == ["org.lwjgl:lwjgl:3.3.3:natives-macos", "org.lwjgl:lwjgl:3.3.3:natives-macos-arm64"]
    assert not any("natives-windows" in n or "natives-linux" in n for n in names)
    assert "ca.weblite:java-objc-bridge:1.1" in names


def test_mac_jvm_args_include_start_on_first_thread(monkeypatch) -> None:
    _mac(monkeypatch, "arm64")
    vanilla = json.loads(VANILLA.read_text(encoding="utf-8"))
    args = install._argument_list(vanilla["arguments"]["jvm"])
    assert "-XstartOnFirstThread" in args
    assert not any("MojangTricksIntelDrivers" in a for a in args)


def test_windows_jvm_args_do_not_get_start_on_first_thread(monkeypatch) -> None:
    monkeypatch.setattr(install.platform, "system", lambda: "Windows")
    monkeypatch.setattr(install.platform, "machine", lambda: "AMD64")
    vanilla = json.loads(VANILLA.read_text(encoding="utf-8"))
    assert "-XstartOnFirstThread" not in install._argument_list(vanilla["arguments"]["jvm"])


def test_mac_java_home_is_used(monkeypatch, tmp_path) -> None:
    home = tmp_path / "temurin-21.jdk" / "Contents" / "Home"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "java").write_text("")
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr(javamod.Path, "exists", lambda self: True if str(self) == "/usr/libexec/java_home" else Path.is_file(self) or Path.is_dir(self))

    def runner(args, **kwargs):
        assert args[0] == "/usr/libexec/java_home"
        return subprocess.CompletedProcess(args, 0, stdout=str(home) + "\n", stderr="")

    found = javamod.find_java(runner=runner, which=lambda name: "/usr/bin/java", mac=True)
    assert found == str(home / "bin" / "java")


def test_mac_stub_java_is_ignored_and_jvm_folder_found(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("JAVA_HOME", raising=False)
    jvm = tmp_path / "JavaVirtualMachines"
    java = jvm / "temurin-21.jdk" / "Contents" / "Home" / "bin" / "java"
    java.parent.mkdir(parents=True)
    java.write_text("")
    monkeypatch.setattr(javamod, "mac_java_home", lambda runner=None: None)
    monkeypatch.setattr(javamod, "MAC_JVM_DIRS", (str(jvm),))
    monkeypatch.setattr(javamod, "mac_jvm_candidates", lambda dirs=None: [str(java)])
    assert javamod.find_java(which=lambda name: "/usr/bin/java", mac=True) == str(java)
    monkeypatch.setattr(javamod, "mac_jvm_candidates", lambda dirs=None: [])
    monkeypatch.setattr(javamod.os.path, "realpath", lambda p: p)
    assert javamod.find_java(which=lambda name: "/usr/bin/java", mac=True) is None
    assert javamod.find_java(which=lambda name: "/opt/homebrew/bin/java", mac=True) == "/opt/homebrew/bin/java"


def test_mac_jvm_candidates_scans_folder(tmp_path) -> None:
    for name in ("temurin-17.jdk", "temurin-21.jdk"):
        java = tmp_path / name / "Contents" / "Home" / "bin" / "java"
        java.parent.mkdir(parents=True)
        java.write_text("")
    found = javamod.mac_jvm_candidates((str(tmp_path),))
    assert found[0].endswith("temurin-21.jdk/Contents/Home/bin/java")
    assert len(found) == 2


def test_mac_app_bundle_uses_application_support(monkeypatch) -> None:
    monkeypatch.delenv("VANTA_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(paths, "repo_root", lambda: Path("/Applications/Vanta.app/Contents/Resources/app"))
    assert paths.default_data_dir() == Path.home() / "Library" / "Application Support" / "Vanta"
    monkeypatch.setattr(paths.platform, "system", lambda: "Windows")
    assert paths.default_data_dir() == Path("/Applications/Vanta.app/Contents/Resources/app/.vanta-data")


def test_first_run_install_only_when_enabled_and_missing(monkeypatch, tmp_path) -> None:
    from vanta.launcher.minecraft import first_run

    calls = []

    def fake_installer(game, download_assets):
        calls.append((game, download_assets))
        jar = game / "1.21.11-fabric" / "client.jar"
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"x")
        return {"instance": str(jar.parent)}

    monkeypatch.delenv("VANTA_AUTO_INSTALL", raising=False)
    assert first_run.maybe_start(tmp_path, installer=fake_installer, background=False) is False
    monkeypatch.setenv("VANTA_AUTO_INSTALL", "1")
    assert first_run.maybe_start(tmp_path, installer=fake_installer, background=False) is True
    assert calls == [(tmp_path / "game", True)]
    status = json.loads((tmp_path / first_run.STATUS_NAME).read_text(encoding="utf-8"))
    assert status["state"] == "done"
    assert first_run.maybe_start(tmp_path, installer=fake_installer, background=False) is False
