"""First-run game download.

Vanta's Windows and macOS downloads ship no game files: no Mojang client.jar
or assets (the Minecraft EULA doesn't allow redistributing them) and no
third-party mods, resource packs or shaders (several licenses forbid it).
When client.jar is missing, Vanta downloads Minecraft
1.21.11 + Fabric (Mojang manifest + Fabric meta, the same installer as
``python -m vanta.launcher.minecraft.install``) in a background thread.
The installer applies the OS rules, so macOS gets the LWJGL macos and
macos-arm64 natives and the -XstartOnFirstThread JVM argument.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

STATUS_NAME = "first-run-install.json"
_lock = threading.Lock()
_running = False


def game_root(data_dir: Path) -> Path:
    settings = Path(data_dir) / "config.json"
    try:
        stored = json.loads(settings.read_text(encoding="utf-8"))
        if isinstance(stored, dict) and stored.get("gameDirectory"):
            return Path(str(stored["gameDirectory"]))
    except (OSError, ValueError):
        pass
    return Path(data_dir) / "game"


def needs_install(data_dir: Path, version: str = "1.21.11") -> bool:
    """True when client.jar is missing, or a first-run download started and never finished.

    Older installs (and the laptop's bundled 1.3.0 folder) have no status file;
    a present client.jar counts as installed for them.
    """
    jar = game_root(data_dir) / f"{version}-fabric" / "client.jar"
    if not (jar.is_file() and jar.stat().st_size > 0):
        return True
    try:
        recorded = json.loads((Path(data_dir) / STATUS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(recorded, dict) and recorded.get("state") not in (None, "done")


WEIGHTS = {"metadata": (0, 2), "client": (2, 8), "libraries": (8, 25), "fabric": (25, 30),
           "assets": (30, 80), "mods": (80, 90), "packs": (90, 99)}


def enabled() -> bool:
    """On by default on Windows; macOS's launcher script sets VANTA_AUTO_INSTALL=1. "0" turns it off."""
    flag = os.environ.get("VANTA_AUTO_INSTALL")
    if flag is not None:
        return flag == "1"
    return sys.platform == "win32"


def status(data_dir: Path) -> dict[str, Any]:
    """What the PLAY tab shows while the first-run download is going."""
    path = Path(data_dir) / STATUS_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"state": "idle"}
    if not isinstance(data, dict):
        data = {"state": "idle"}
    with _lock:
        data["running"] = _running
    if data.get("state") == "downloading" and not data["running"]:
        data["state"] = "interrupted"  # app closed mid-download; it resumes on next start
    data["needed"] = needs_install(data_dir)
    return data


def maybe_start(data_dir: Path, *, installer: Callable[..., dict[str, Any]] | None = None, background: bool = True) -> bool:
    """Start the download if enabled and needed. Returns True when it started."""
    global _running
    if not enabled() or not needs_install(data_dir):
        return False
    with _lock:
        if _running:
            return False
        _running = True

    def _work() -> None:
        global _running
        status = Path(data_dir) / STATUS_NAME
        log = Path(data_dir) / "install.log"
        status.parent.mkdir(parents=True, exist_ok=True)

        def _write(state: str, **extra: Any) -> None:
            payload = {"state": state, "time": datetime.now().astimezone().isoformat(timespec="seconds"), **extra}
            tmp = status.with_name(status.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, status)

        _write("downloading", step="metadata", done=0, total=1, percent=0)
        last = [0.0]

        def progress(step: str, done: int, total: int) -> None:
            import time as _t
            lo, hi = WEIGHTS.get(step, (0, 99))
            frac = (done / total) if total else 1.0
            now = _t.monotonic()
            if now - last[0] < 0.5 and done not in (0, total):
                return
            last[0] = now
            _write("downloading", step=step, done=done, total=total, percent=int(lo + (hi - lo) * min(1.0, frac)))

        try:
            run = installer
            if run is None:
                from vanta.launcher.minecraft.install import install_client as run
            try:
                result = run(game_root(data_dir), download_assets=True, progress=progress)
            except TypeError:
                result = run(game_root(data_dir), download_assets=True)
            visual = result.get("visualSet") or {}
            skipped = [f"{v.get('title')}: {v.get('reason')}" for v in visual.get("skipped") or []]
            _write("done", percent=100, instance=str(result.get("instance")), skipped=skipped)
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, default=str)[:4000] + "\n")
        except Exception as exc:  # noqa: BLE001 - report, never crash the UI
            _write("failed", error=str(exc))
            with log.open("a", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
        finally:
            with _lock:
                _running = False

    if background:
        threading.Thread(target=_work, name="vanta-first-run-install", daemon=True).start()
    else:
        _work()
    return True
