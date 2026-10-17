"""Mac hardware facts Vanta needs: Intel vs Apple Silicon, Rosetta, RAM, Intel iGPU.

Every probe takes injectable inputs so tests run on any OS. On non-Macs the
answers describe the current machine without calling macOS tools.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
from functools import lru_cache
from typing import Any, Callable

Runner = Callable[..., Any]

# Mojang JSON / Adoptium-style arch names used inside Vanta.
X64 = "x64"
ARM64 = "arm64"


def norm_arch(machine: str) -> str:
    m = (machine or "").lower()
    if m in {"x86_64", "amd64", "x64", "i386"}:  # some macOS tools report i386 on Intel
        return X64
    if m in {"arm64", "aarch64", "armv8"}:
        return ARM64
    return m


def _sysctl(name: str, runner: Runner) -> str:
    try:
        done = runner(["/usr/sbin/sysctl", "-n", name], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (getattr(done, "stdout", "") or "").strip() if getattr(done, "returncode", 1) == 0 else ""


def probe(*, system: str | None = None, machine: str | None = None, runner: Runner = subprocess.run,
          mac_ver: str | None = None) -> dict[str, Any]:
    """{'mac', 'hardware', 'process', 'rosetta', 'intel', 'macos', 'memGb', 'cpu'}

    hardware: the real CPU (arm64 even when this process runs under Rosetta).
    process:  what this Python process is (x64 when translated by Rosetta).
    """
    system = system if system is not None else platform.system()
    process = norm_arch(machine if machine is not None else platform.machine())
    info: dict[str, Any] = {"mac": system == "Darwin", "hardware": process, "process": process, "rosetta": False,
                            "intel": False, "macos": "", "memGb": 0, "cpu": ""}
    if not info["mac"]:
        return info
    translated = _sysctl("sysctl.proc_translated", runner) == "1"
    arm_hw = translated or _sysctl("hw.optional.arm64", runner) == "1"
    info["hardware"] = ARM64 if arm_hw else (X64 if process == X64 else process)
    info["rosetta"] = translated
    info["intel"] = info["hardware"] == X64
    info["macos"] = mac_ver if mac_ver is not None else platform.mac_ver()[0]
    try:
        info["memGb"] = round(int(_sysctl("hw.memsize", runner) or 0) / 1024 ** 3)
    except ValueError:
        info["memGb"] = 0
    info["cpu"] = _sysctl("machdep.cpu.brand_string", runner)
    return info


@lru_cache(maxsize=1)
def current() -> dict[str, Any]:
    return probe()


def macos_major(version: str) -> int:
    try:
        return int((version or "0").split(".")[0])
    except ValueError:
        return 0


# ---- GPU ---------------------------------------------------------------------

def parse_displays(profiler_json: str) -> list[str]:
    """GPU model names from `system_profiler -json SPDisplaysDataType`."""
    try:
        data = json.loads(profiler_json or "{}")
    except ValueError:
        return []
    return [str(gpu.get("sppci_model") or gpu.get("_name") or "") for gpu in data.get("SPDisplaysDataType") or []
            if isinstance(gpu, dict)]


def is_intel_igpu(gpus: list[str]) -> bool:
    """Only an Intel iGPU (Iris, UHD, HD Graphics) and no AMD/NVIDIA discrete GPU."""
    low = [g.lower() for g in gpus if g]
    if not low:
        return False
    has_discrete = any(re.search(r"\b(amd|radeon|nvidia|geforce|quadro)\b", g) for g in low)
    has_intel = any("intel" in g or re.search(r"\b(iris|uhd|hd graphics)\b", g) for g in low)
    return has_intel and not has_discrete


@lru_cache(maxsize=1)
def intel_igpu_only() -> bool:
    info = current()
    if not (info["mac"] and info["intel"]):
        return False
    try:
        done = subprocess.run(["/usr/sbin/system_profiler", "-json", "SPDisplaysDataType"],
                              capture_output=True, text=True, timeout=15)
        gpus = parse_displays(done.stdout)
        return is_intel_igpu(gpus) if gpus else True
    except (OSError, subprocess.SubprocessError):
        return True  # unknown Intel Mac: assume the common integrated GPU and be gentle


# ---- Defaults for Intel Macs -------------------------------------------------

def recommended_ram_gb(mem_gb: int, *, intel_mac: bool) -> int:
    """Minecraft heap that leaves room for macOS and the (shared-memory) GPU."""
    if mem_gb <= 0:
        return 4
    if intel_mac:
        if mem_gb <= 8:
            return 3
        if mem_gb <= 16:
            return 4
        return 6
    return 4 if mem_gb <= 16 else 6


# Low-end defaults seeded into a NEW instance's options.txt on Intel Macs with only an iGPU.
INTEL_IGPU_OPTIONS = {
    "renderDistance": "8",
    "simulationDistance": "6",
    "graphicsMode": "0",        # Fast
    "maxFps": "60",
    "enableVsync": "true",
    "entityShadows": "false",
    "particles": "1",           # Decreased
    "biomeBlendRadius": "1",
    "mipmapLevels": "2",
    "entityDistanceScaling": "0.75",
}

TRAILER_IGPU_WARNING = ("This Mac has Intel integrated graphics. Trailer style turns on Iris shaders and animated packs, "
                        "which usually run at low FPS on Intel Iris/UHD. Fast is recommended; you can switch Trailer style "
                        "off later in INSTANCES.")
