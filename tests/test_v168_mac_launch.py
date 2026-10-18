"""macOS launch path, simulated on Linux: no /proc, ps for command lines, Popen as the source of truth.

Before 1.6.8 a healthy game on macOS was stopped 0.6 s after PLAY with
"The Java process exited before it stayed alive (exit None)": the liveness check read
/proc/<pid>/cmdline, which macOS doesn't have, so it always said "not our process".
"""
import os
import signal
import stat
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from vanta.launcher.minecraft import launch, monitor
from vanta.shared import java_download

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process handling")


class Log:
    def __init__(self):
        self.lines = []

    def info(self, m):
        self.lines.append(("info", m))

    def error(self, m):
        self.lines.append(("error", m))

    warning = info


def fake_java(tmp_path, body):
    """A 'java' that behaves like the JVM for these tests (a shell script)."""
    home = tmp_path / "runtime" / "temurin-21-arm64" / "jdk-21.jdk" / "Contents" / "Home" / "bin"
    home.mkdir(parents=True)
    java = home / "java"
    java.write_text("#!/bin/sh\n" + body + "\n")
    java.chmod(0o755)
    return java


def instance(tmp_path):
    inst = tmp_path / "game" / "1.21.11-fabric"
    inst.mkdir(parents=True)
    (inst / "client.jar").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    return inst


def run(tmp_path, monkeypatch, java, *, mac=True):
    inst = instance(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(launch, "java_for_instance", lambda *a: (str(java), ""))
    monkeypatch.setattr("vanta.shared.macplatform.current",
                        lambda: {"mac": mac, "hardware": "arm64", "process": "arm64", "rosetta": False, "intel": False})
    # macOS has no /proc: make every /proc read fail like it does there.
    real = launch._posix_command_line
    monkeypatch.setattr(launch, "_posix_command_line", lambda pid, **k: real(pid, proc=tmp_path / "no-proc",
                                                                              runner=k.get("runner")))
    log = Log()
    res = launch._launch_local(config=None, log=log, data_dir=data, account={"id": "a", "label": "UHDGAMING", "username": "UHDGAMING", "uuid": "0" * 32, "mode": "local"},
                               version={"versionNumber": "1.21.11", "loader": "fabric", "gameDirectory": str(inst), "downloaded": True},
                               saved_profile={"name": "Vanta Performance", "intent": "performance", "ramGb": 2}, ram_value=2)
    return res, data, log


def test_running_game_is_not_stopped_without_proc(tmp_path, monkeypatch):
    java = fake_java(tmp_path, 'echo "Setting user: UHDGAMING"; sleep 5')
    res, data, log = run(tmp_path, monkeypatch, java)
    try:
        assert res["launched"] is True, res
        pid = res["pid"]
        assert launch._pid_alive(pid) and launch._cmdline_matches(pid, str(Path(data).parent / "game/1.21.11-fabric/client.jar"))
        status = monitor.game_status(data)
        assert status["running"] is True and status["crash"] is None
        text = (data / "client.log").read_text()
        assert "Java: " in text and "System: " in text and "Command: " in text
    finally:
        launch._signal_stop(res.get("pid") or 0)


def test_real_early_exit_reports_code_and_log(tmp_path, monkeypatch):
    java = fake_java(tmp_path, 'echo "Error: Could not find or load main class net.minecraft.client.main.Main" >&2; exit 1')
    res, data, log = run(tmp_path, monkeypatch, java)
    assert res["launched"] is False
    assert "exit code 1" in res["error"] and "None" not in res["error"]
    assert "Could not find or load main class" in (data / "client.log").read_text()  # stderr goes to the game log


def test_killed_by_signal_is_plain_words():
    assert launch.exit_text(-signal.SIGKILL) == "stopped by SIGKILL"
    assert launch.exit_text(0) == "exit code 0"
    assert launch.exit_text(None) == "still running"


def test_exited_child_is_not_alive_even_unreaped(tmp_path):
    p = subprocess.Popen(["sh", "-c", "exit 3"])
    launch._PROCESSES[p.pid] = p
    time.sleep(0.3)
    assert launch._pid_alive(p.pid) is False and launch.exit_code(p.pid) == 3


def test_ps_fallback_and_spaces_in_path(tmp_path):
    jar = "/Users/u/Library/Application Support/Vanta/game/1.21.11-fabric/client.jar"
    line = ("/Users/u/Library/Application Support/Vanta/runtime/temurin-21-arm64/jdk-21.jdk/Contents/Home/bin/java "
            f"-Dvanta.clientJar={jar} -XstartOnFirstThread -Xmx4G -cp a.jar:b.jar net.fabricmc.loader.impl.launch.knot.KnotClient")
    runner = lambda cmd, **k: SimpleNamespace(returncode=0, stdout=line + "\n")
    got = launch._posix_command_line(4242, proc=tmp_path / "no-proc", runner=runner)
    assert got == line
    orig = launch._posix_command_line
    try:
        launch._posix_command_line = lambda pid, **k: line
        assert launch._cmdline_matches(4242, jar) is True
        launch._posix_command_line = lambda pid, **k: line.replace("bin/java", "bin/python3")
        assert launch._cmdline_matches(4242, jar) is False
    finally:
        launch._posix_command_line = orig


def test_header_hides_token_and_shortens_classpath():
    h = launch.launch_header("/x/java", ["/x/java", "-cp", os.pathsep.join(["a.jar", "b.jar", "c.jar"]), "Main", "--accessToken", "SECRET"])
    assert "<3 jars>" in h and "SECRET" not in h and "<hidden>" in h


def test_make_runnable_fixes_modes_and_unquarantines(tmp_path):
    home = tmp_path / "temurin-21-arm64" / "jdk-21.jdk" / "Contents" / "Home"
    (home / "bin").mkdir(parents=True)
    (home / "lib").mkdir()
    for f in (home / "bin" / "java", home / "bin" / "keytool", home / "lib" / "jspawnhelper"):
        f.write_text("x")
        f.chmod(0o644)
    calls = []
    java_download.make_runnable(home / "bin" / "java", mac=True, runner=lambda cmd, **k: calls.append(cmd))
    for f in (home / "bin" / "java", home / "bin" / "keytool", home / "lib" / "jspawnhelper"):
        assert f.stat().st_mode & stat.S_IXUSR
    assert calls == [["xattr", "-dr", "com.apple.quarantine", str(tmp_path / "temurin-21-arm64" / "jdk-21.jdk")]]


def test_mac_flags_once_and_classpath_separator():
    cmd = launch.mac_jvm_flags(["java", "-Dvanta.clientJar=/c.jar", "-XstartOnFirstThread", "-cp", "a:b", "Main"],
                               {"mac": True}, 4)
    assert cmd.count("-XstartOnFirstThread") == 1 and cmd[2] == "-XstartOnFirstThread"
    if os.name != "nt":
        assert os.pathsep == ":"
