"""Windows-safe JSON writes (the laptop hit WinError 5 on profiles.json)."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from vanta.shared.utilities import jsonio


def test_replace_retried_when_windows_denies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "profiles.json"
    real = Path.replace
    fails = [3]

    def flaky(self: Path, dst: Path) -> Path:
        if fails[0]:
            fails[0] -= 1
            raise PermissionError(5, "Access is denied")
        return real(self, dst)

    monkeypatch.setattr(Path, "replace", flaky)
    jsonio.write_json(target, {"profiles": [1]})
    assert jsonio.read_json(target, None) == {"profiles": [1]}
    assert not list(tmp_path.glob("*.tmp"))


def test_gives_up_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(self: Path, dst: Path) -> Path:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "replace", deny)
    monkeypatch.setattr(jsonio.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        jsonio.write_json(tmp_path / "x.json", {"a": 1})
    assert not list(tmp_path.glob("*.tmp"))


def test_unchanged_write_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "p.json"
    jsonio.write_json(target, {"a": 1})
    monkeypatch.setattr(Path, "replace", lambda self, dst: (_ for _ in ()).throw(AssertionError("rewrote")))
    jsonio.write_json(target, {"a": 1})


def test_concurrent_readers_and_writers(tmp_path: Path) -> None:
    target = tmp_path / "profiles.json"
    errors: list[BaseException] = []

    def work(n: int) -> None:
        try:
            for i in range(50):
                jsonio.write_json(target, {"n": n, "i": i})
                assert isinstance(jsonio.read_json(target, {}), dict)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
