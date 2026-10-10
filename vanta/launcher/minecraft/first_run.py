"""First-run game download for platforms without a bundled game folder (macOS).

The Windows build ships its game folder. The macOS app does not, so when
VANTA_AUTO_INSTALL=1 and client.jar is missing, Vanta downloads Minecraft
1.21.11 + Fabric (Mojang manifest + Fabric meta, the same installer as
``python -m vanta.launcher.minecraft.install``) in a background thread.
The installer applies the OS rules, so macOS gets the LWJGL macos and
macos-arm64 natives and the -XstartOnFirstThread JVM argument.
"""

from __future__ import annotations

import json
import os
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
    jar = game_root(data_dir) / f"{version}-fabric" / "client.jar"
    return not (jar.is_file() and jar.stat().st_size > 0)


def maybe_start(data_dir: Path, *, installer: Callable[..., dict[str, Any]] | None = None, background: bool = True) -> bool:
    """Start the download if enabled and needed. Returns True when it started."""
    global _running
    if os.environ.get("VANTA_AUTO_INSTALL") != "1" or not needs_install(data_dir):
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
            status.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        _write("downloading")
        try:
            run = installer
            if run is None:
                from vanta.launcher.minecraft.install import install_client as run
            result = run(game_root(data_dir), download_assets=True)
            _write("done", instance=str(result.get("instance")))
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
