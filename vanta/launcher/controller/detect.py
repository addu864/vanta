"""Look for a gamepad without inventing one.

Order: pygame if it is already importable, then /dev/input/js* character
devices, then evdev if it is already importable. Nothing here installs a
package. A normal file named js0 is not a controller.
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

DEFAULT_INPUT_ROOT = Path("/dev/input")
STATUS_NO_DEVICE = "NO_DEVICE"
STATUS_PRESENT = "PRESENT"


def detect_controllers(*, input_root: Path | None = None) -> dict[str, Any]:
    root = DEFAULT_INPUT_ROOT if input_root is None else Path(input_root)
    pygame_check, pygame_devices = _check_pygame()
    js_check, js_devices = _check_js(root)
    evdev_check, evdev_devices = _check_evdev()
    checks = [pygame_check, js_check, evdev_check]
    devices = [*pygame_devices, *js_devices, *evdev_devices]
    found = len(devices) > 0
    phrases = ", ".join(f"{item['name']} ({item['detail']})" for item in checks)
    if found:
        ending = "A controller was reported by one of those checks."
    else:
        ending = "No controller was found."
    return {
        "status": STATUS_PRESENT if found else STATUS_NO_DEVICE,
        "deviceFound": found,
        "devices": devices,
        "checks": checks,
        "checkSummary": f"Checked {phrases}. {ending}",
        "testedInMinecraft": False,
        "inGameControllerPlayTested": False,
    }


def _check_pygame() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        import pygame
    except ImportError:
        return (
            {
                "name": "pygame",
                "ran": True,
                "available": False,
                "detail": "pygame is not installed",
            },
            [],
        )
    devices: list[dict[str, Any]] = []
    try:
        if not pygame.get_init():
            pygame.init()
        if not pygame.joystick.get_init():
            pygame.joystick.init()
        count = int(pygame.joystick.get_count())
        for index in range(count):
            joystick = pygame.joystick.Joystick(index)
            joystick.init()
            name = str(joystick.get_name() or "").strip() or f"joystick-{index}"
            devices.append({"source": "pygame", "index": index, "name": name})
        detail = f"pygame joystick count is {count}"
    except Exception as exc:
        devices = []
        detail = f"pygame joystick check failed ({exc.__class__.__name__})"
    return (
        {"name": "pygame", "ran": True, "available": True, "detail": detail},
        devices,
    )


def _check_js(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    label = "/dev/input/js*"
    if not root.exists():
        return (
            {
                "name": label,
                "ran": True,
                "available": False,
                "count": 0,
                "detail": f"{root} is not present",
            },
            [],
        )
    if not root.is_dir():
        return (
            {
                "name": label,
                "ran": True,
                "available": False,
                "count": 0,
                "detail": f"{root} is not a directory",
            },
            [],
        )
    devices: list[dict[str, Any]] = []
    ignored = 0
    for path in sorted(root.iterdir()):
        if not path.name.startswith("js"):
            continue
        if not _is_char_device(path):
            ignored += 1
            continue
        devices.append(
            {
                "source": "/dev/input",
                "path": str(path),
                "name": _kernel_name(path, root),
            }
        )
    detail = f"scanned {root} for js* character devices; found {len(devices)}"
    if ignored:
        detail += f"; ignored {ignored} non-device js* path(s)"
    return (
        {
            "name": label,
            "ran": True,
            "available": True,
            "count": len(devices),
            "detail": detail,
        },
        devices,
    )


def _check_evdev() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        import evdev
        from evdev import ecodes
    except ImportError:
        return (
            {
                "name": "evdev",
                "ran": True,
                "available": False,
                "detail": "evdev is not installed",
            },
            [],
        )
    devices: list[dict[str, Any]] = []
    try:
        paths = list(evdev.list_devices())
        for path in paths:
            device = evdev.InputDevice(path)
            keys = device.capabilities().get(ecodes.EV_KEY, [])
            gamepad = ecodes.BTN_GAMEPAD in keys or ecodes.BTN_SOUTH in keys
            if not gamepad:
                continue
            name = str(getattr(device, "name", "") or "").strip() or path
            devices.append({"source": "evdev", "path": path, "name": name})
        detail = f"evdev gamepad count is {len(devices)}"
    except Exception as exc:
        devices = []
        detail = f"evdev check failed ({exc.__class__.__name__})"
    return (
        {"name": "evdev", "ran": True, "available": True, "detail": detail},
        devices,
    )


def _is_char_device(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISCHR(mode)


def _kernel_name(path: Path, root: Path) -> str:
    """Read the kernel name only for a real node under /dev/input.

    A caller-supplied folder (tests, or anything that is not /dev/input) keeps
    the node name. File contents are never used as a controller name.
    """
    try:
        default_root = DEFAULT_INPUT_ROOT.resolve()
        same_root = root.resolve() == default_root
    except OSError:
        same_root = False
    if not same_root:
        return path.name
    sysfs = Path("/sys/class/input") / path.name / "device" / "name"
    try:
        if sysfs.is_file():
            text = sysfs.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    except OSError:
        pass
    return path.name
