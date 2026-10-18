"""v1.7.0 platform sweep: macOS-without-/proc process checks and native dialogs."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from vanta.launcher.servers import manager
from vanta.shared import dialogs


class Done:
    def __init__(self, out: str, code: int = 0) -> None:
        self.stdout, self.returncode = out, code


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_finished_child_is_not_running_without_proc(monkeypatch) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    time.sleep(0.5)  # exited, not reaped: a zombie
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda self: False if str(self).startswith("/proc/") else real_is_file(self))
    assert manager._pid_alive(proc.pid) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_running_child_is_running_without_proc(monkeypatch) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        real_is_file = Path.is_file
        monkeypatch.setattr(Path, "is_file", lambda self: False if str(self).startswith("/proc/") else real_is_file(self))
        assert manager._pid_alive(proc.pid) is True
    finally:
        proc.kill(); proc.wait()


def test_ps_zombie_reads_state() -> None:
    assert manager._ps_zombie(1, runner=lambda *a, **k: Done("Z+\n")) is True
    assert manager._ps_zombie(1, runner=lambda *a, **k: Done("S\n")) is False
    def boom(*a, **k):
        raise FileNotFoundError("ps")
    assert manager._ps_zombie(1, runner=boom) is False


def test_mac_dialog_uses_osascript() -> None:
    seen = {}
    def run(cmd, **k):
        seen["cmd"] = cmd
        return Done("/Users/a/Pictures/sky.png\n")
    path, err = dialogs.pick("image", "Pick", platform="darwin", runner=run)
    assert (path, err) == ("/Users/a/Pictures/sky.png", "")
    assert seen["cmd"][0] == "osascript" and "choose file" in seen["cmd"][2]
    path, _ = dialogs.pick("folder", "Pick", platform="darwin", runner=lambda c, **k: Done("/Users/a/mc/\n"))
    assert path == "/Users/a/mc"


def test_mac_dialog_cancel_is_not_a_choice() -> None:
    path, err = dialogs.pick("folder", "Pick", platform="darwin", runner=lambda c, **k: Done("", 1))
    assert path == "" and "No folder" in err


def test_linux_dialog_without_tools_says_so() -> None:
    path, err = dialogs.pick("image", "Pick", platform="linux", which=lambda n: None)
    assert path == "" and "zenity" in err


def test_linux_dialog_uses_zenity() -> None:
    seen = {}
    def run(cmd, **k):
        seen["cmd"] = cmd
        return Done("/home/a/mc\n")
    path, _ = dialogs.pick("folder", "Pick", platform="linux", which=lambda n: "/usr/bin/zenity" if n == "zenity" else None, runner=run)
    assert path == "/home/a/mc" and "--directory" in seen["cmd"]
