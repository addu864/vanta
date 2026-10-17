"""Performance auto-tune: a RAM suggestion from the PC's memory and what's in the instance."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def system_memory_gb() -> float:
    """Total physical RAM in GB, or 0 when it can't be read."""
    try:
        if sys.platform == "win32":
            class MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MemStatus()
            st.dwLength = ctypes.sizeof(MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):  # type: ignore[attr-defined]
                return st.ullTotalPhys / 1024 ** 3
            return 0.0
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return int(out.strip() or 0) / 1024 ** 3
        if hasattr(os, "sysconf"):
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        pass
    return 0.0


def count_content(instance: Path) -> dict[str, int]:
    inst = Path(instance)
    def n(folder: str, suffix: str) -> int:
        d = inst / folder
        return sum(1 for f in d.iterdir() if f.is_file() and f.name.lower().endswith(suffix)) if d.is_dir() else 0
    return {"mods": n("mods", ".jar"), "shaders": n("shaderpacks", ".zip"), "resourcepacks": n("resourcepacks", ".zip")}


def recommend(mods: int, shaders: int = 0, resourcepacks: int = 0, total_gb: float = 0.0, intel_mac: bool = False) -> dict[str, Any]:
    reasons: list[str] = []
    if mods >= 200:
        want, why = 8, f"{mods} mods (a big modpack)"
    elif mods >= 100:
        want, why = 6, f"{mods} mods"
    elif mods >= 30 or shaders:
        want, why = 4, (f"{mods} mods" + (" and shaders" if shaders else ""))
    else:
        want, why = 3, f"{mods} mods (light)"
    reasons.append(why)
    if resourcepacks >= 4 and want < 6:
        want += 1
        reasons.append(f"{resourcepacks} resource packs")
    if total_gb > 0:
        # Leave room for Windows/macOS, the browser and Discord: never more than half, at least 2 GB free.
        cap = max(2, min(int(total_gb // 2), int(total_gb) - 2))
        if intel_mac:
            cap = max(2, min(cap, int(total_gb * 0.4)))
        if want > cap:
            reasons.append(f"capped to {cap} GB because this PC has {round(total_gb)} GB")
            want = cap
        else:
            reasons.append(f"this PC has {round(total_gb)} GB")
    want = max(2, min(16, want))
    note = "More RAM than this rarely helps and can cause longer pauses." if want >= 6 else ""
    return {"ramGb": want, "reason": ", ".join(reasons), "note": note, "totalGb": round(total_gb, 1)}
