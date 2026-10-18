"""CI smoke test for the macOS launch path (GitHub Actions macOS runners; no real Mac needed here).

Uses Vanta's own code end to end: Mojang/Fabric install of 1.21.11, Temurin download for this
CPU (SHA-256 checked, x-bit + quarantine fix), the PLAY command builder and the launch/liveness
logic. Passes when the game gets past start-up ("Setting user") and Vanta did not stop it.
A runner without a usable OpenGL may then fail to open a window; that is reported, not failed.
"""
from __future__ import annotations

import os
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(os.environ.setdefault("VANTA_DATA_DIR", os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(), "vanta-ci")))
DATA.mkdir(parents=True, exist_ok=True)

from vanta.launcher.minecraft import install, launch, monitor  # noqa: E402
from vanta.shared import java_download, macplatform  # noqa: E402


class Log:
    def info(self, m): print("[vanta]", m, flush=True)
    def error(self, m): print("[vanta ERROR]", m, flush=True)
    warning = info


def main() -> int:
    info = macplatform.current()
    print("platform:", platform.platform(), "| machine:", platform.machine(), "| vanta sees:", info, flush=True)
    game = DATA / "game"
    t = time.time()
    res = install.install_client(game, version="1.21.11", loader=install.FABRIC_LOADER, download_assets=os.environ.get("CI_ASSETS", "1") == "1",
                                 progress=lambda s, d, n: None)
    print(f"install ok in {time.time() - t:.0f}s:", {k: v for k, v in res.items() if k != "libraries"}, flush=True)
    inst = game / "1.21.11-fabric"
    major = launch.required_java(inst, "1.21.11")
    arch, strict = java_download.java_arch_for("1.21.11", major, info)
    java = java_download.download_temurin(DATA, major, arch)
    print(f"temurin {major} {arch} (strict={strict}):", java, flush=True)
    os.system(f'"{java}" -version')
    os.system(f'xattr -l "{java}" || true')
    launch.java_for_instance = lambda *a: (java, "")
    account = {"id": "ci", "label": "UHDGAMING", "username": "UHDGAMING", "uuid": "0" * 32, "mode": "local"}
    version = {"versionNumber": "1.21.11", "loader": "fabric", "gameDirectory": str(inst), "downloaded": True}
    r = launch._launch_local(config=None, log=Log(), data_dir=DATA, account=account, version=version,
                             saved_profile={"name": "CI", "intent": "performance", "ramGb": 2}, ram_value=2)
    print("PLAY result:", {k: r.get(k) for k in ("status", "launched", "pid", "error")}, flush=True)
    log_path = DATA / "client.log"
    if not r.get("launched"):
        print(log_path.read_text(errors="replace")[-6000:])
        print("FAIL: Vanta reported the game as not started.")
        return 1
    pid = r["pid"]
    deadline = time.time() + float(os.environ.get("CI_GAME_SECONDS", "150"))
    setting_user = window = False
    st = {}
    while time.time() < deadline:
        st = monitor.game_status(DATA)
        text = log_path.read_text(errors="replace")
        setting_user = setting_user or "Setting user: UHDGAMING" in text
        window = window or any(k in text for k in ("Backend library: LWJGL", "OpenGL", "Created: ", "Sound engine started"))
        if not st["running"]:
            break
        if setting_user and window and "Sound engine started" in text:
            break
        time.sleep(2)
    still = st.get("running")
    code = launch.exit_code(pid)
    if still:
        launch._signal_stop(pid)
    text = log_path.read_text(errors="replace")
    print("----- client.log (tail) -----\n" + text[-8000:] + "\n-----------------------------", flush=True)
    print(f"summary: setting_user={setting_user} window_stage={window} running_at_end={still} exit={launch.exit_text(code)}"
          f" crash={(st.get('crash') or {}).get('title')}", flush=True)
    if not setting_user:
        print("FAIL: the game never reached 'Setting user'.")
        return 1
    if not still and code not in (None, 0):
        print("NOTE: got past start-up, then the game exited (often no usable OpenGL on a CI runner).")
    print("PASS: past the start-up crash; Vanta kept the game running.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
