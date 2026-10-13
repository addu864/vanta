"""Vanta Companion: Vanta's own in-game menu mod (source: addu864/vanta companion/).

Shipped inside the app (it's Vanta's code, not a third-party mod) and placed in
Fabric instances whose Minecraft version the jar supports. A user who disabled it
(vanta-companion-*.jar.disabled) keeps it disabled. Older copies are replaced.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

BUNDLED = Path(__file__).resolve().parent / "bundled"
PREFIX = "vanta-companion-"
SUPPORTED = {"1.21.11"}


def bundled_jar() -> Path | None:
    jars = sorted(BUNDLED.glob(PREFIX + "*.jar"))
    return jars[-1] if jars else None


def jar_info(jar: Path) -> dict[str, Any]:
    with zipfile.ZipFile(jar) as zf:
        return json.loads(zf.read("fabric.mod.json").decode("utf-8"))


def supports(game_version: str) -> bool:
    return game_version in SUPPORTED


MIN_FABRIC_API = (0, 141, 6)


def fabric_api_version(mods_dir: Path) -> tuple[int, ...] | None:
    """Version of the enabled Fabric API jar in mods_dir, or None."""
    if not mods_dir.is_dir():
        return None
    for jar in mods_dir.glob("*.jar"):
        if "fabric-api" not in jar.name.lower() and "fabric_api" not in jar.name.lower():
            continue
        try:
            info = jar_info(jar)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
        if info.get("id") == "fabric-api":
            nums = re.findall(r"\d+", str(info.get("version") or "").split("+")[0])
            return tuple(int(n) for n in nums[:3])
    return None


def ensure_companion(mods_dir: Path, game_version: str, loader: str = "fabric") -> dict[str, Any]:
    if loader.lower() != "fabric" or not supports(game_version):
        return {"ok": True, "installed": False, "reason": f"No Vanta Companion build for {game_version}/{loader}."}
    jar = bundled_jar()
    if jar is None:
        return {"ok": False, "installed": False, "reason": "Companion jar missing from this Vanta build."}
    api = fabric_api_version(mods_dir)
    if api is None or api < MIN_FABRIC_API:
        return {"ok": True, "installed": False, "reason": "Needs Fabric API 0.141.6 or newer in this instance."}
    existing = [p for p in mods_dir.iterdir() if p.name.startswith(PREFIX) and re.search(r"\.jar(\.disabled)?$", p.name)]
    if any(p.name.endswith(".disabled") for p in existing):
        return {"ok": True, "installed": False, "reason": "Disabled by the player."}
    if any(p.name == jar.name for p in existing):
        return {"ok": True, "installed": True, "file": jar.name, "changed": False}
    for old in existing:
        try:
            old.unlink()
        except OSError:
            pass
    tmp = mods_dir / (jar.name + ".part")
    shutil.copyfile(jar, tmp)
    tmp.replace(mods_dir / jar.name)
    return {"ok": True, "installed": True, "file": jar.name, "changed": True}
