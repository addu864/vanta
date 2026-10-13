"""Crash summaries from real-world output (v1.3.1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vanta.launcher.minecraft import launch, monitor

FABRIC_LOG = """--- local start 2026-10-13T18:05:23+05:30 ---
[18:05:24] [main/WARN]: Mod resolution failed
[18:05:24] [main/ERROR]: Incompatible mods found!
net.fabricmc.loader.impl.FormattedException: Some of your mods are incompatible with the game or each other!
A potential solution has been determined, this may resolve your problem:
\t - Install definitely_missing_mod, version 1.0 or later.
More details:
\t - Mod 'Vanta crash test' (vanta_crash_test) 1.0.0 requires version 1.0 or later of definitely_missing_mod, which is missing!
\tat net.fabricmc.loader.impl.FabricLoaderImpl.load(FabricLoaderImpl.java:202)
"""


def test_fabric_incompatible_mods_summary() -> None:
    s = monitor.summarize_crash(FABRIC_LOG.splitlines())
    assert s["title"] == "Mods are incompatible"
    assert "disable the last mod" in s["hint"]
    assert any("which is missing" in d for d in s["details"])


def _session(tmp: Path, pid: int) -> None:
    (tmp / launch.SESSION_NAME).write_text(json.dumps({"pid": pid, "jar": str(tmp / "i" / "client.jar"), "started": 1.0}))
    (tmp / "client.log").write_text(FABRIC_LOG)


def test_crash_found_even_if_never_seen_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "_pid_alive", lambda pid: False)
    monitor._state.update(pid=0, crash=None)
    _session(tmp_path, 4242)
    st = monitor.game_status(tmp_path)
    assert st["running"] is False and st["crash"]["title"] == "Mods are incompatible"
    assert not (tmp_path / launch.SESSION_NAME).exists()


def test_user_stop_is_not_a_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "_pid_alive", lambda pid: False)
    monitor._state.update(pid=0, crash=None)
    _session(tmp_path, 4343)
    launch.STOPPED_BY_USER.add(4343)
    assert monitor.game_status(tmp_path)["crash"] is None


def test_nonzero_exit_code_counts_as_crash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(launch, "exit_code", lambda pid: 137)
    monitor._state.update(pid=0, crash=None)
    (tmp_path / launch.SESSION_NAME).write_text(json.dumps({"pid": 4444, "jar": "x", "started": 1.0}))
    (tmp_path / "client.log").write_text("--- local start x ---\n[main/INFO]: Loading\n")
    st = monitor.game_status(tmp_path)
    assert st["crash"] and st["crash"]["exitCode"] == 137
