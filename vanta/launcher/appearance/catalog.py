"""Original Vanta resource packs and shader-preset descriptions.

Resource pack format for Minecraft Java 1.21.11 is 75.0. That number is the
resource-pack row on the Minecraft Wiki pack-format page (Java Edition 1.21.11),
not the data-pack format 94.1. Since snapshot 25w31a, a pack that does not
support the legacy range (resource pack format below 65) must set min_format
and max_format and must not include pack_format or supported_formats.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from vanta.shared.config.paths import repo_root

# [major, minor]. 75.0 is the documented resource pack format for 1.21.11.
PACK_FORMAT_MAJOR = 75
PACK_FORMAT_MINOR = 0
PACK_FORMAT = [PACK_FORMAT_MAJOR, PACK_FORMAT_MINOR]

TEXTURE_SIZES: dict[str, tuple[int, int]] = {
    "pack.png": (64, 64),
    "assets/minecraft/textures/block/dirt.png": (16, 16),
    "assets/minecraft/textures/block/oak_log.png": (16, 16),
    "assets/minecraft/textures/block/coal_ore.png": (16, 16),
    "assets/minecraft/textures/gui/vanta_button.png": (32, 16),
}


@dataclass(frozen=True)
class PackSpec:
    id: str
    name: str
    summary: str


PACKS: tuple[PackSpec, ...] = (
    PackSpec(
        id="vanta-vanilla-plus",
        name="Vanta Vanilla+",
        summary="Slightly varied original textures. Not a complete pack. Not tested in Minecraft.",
    ),
    PackSpec(
        id="vanta-cartoon",
        name="Vanta Cartoon",
        summary="Bright, chunky, original textures. Not a complete pack. Not tested in Minecraft.",
    ),
)

PACKS_BY_ID = {item.id: item for item in PACKS}


def resourcepacks_root() -> Path:
    return repo_root() / "assets" / "resourcepacks"


def pack_source(pack_id: str) -> Path:
    return resourcepacks_root() / pack_id


def shaders_root() -> Path:
    return repo_root() / "assets" / "shaders"


def load_shader_presets() -> dict[str, dict[str, object]]:
    """Read Vanta-owned JSON descriptions. These are not shader programs."""
    presets: dict[str, dict[str, object]] = {}
    root = shaders_root()
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        preset_id = data.get("id")
        if not isinstance(preset_id, str) or not preset_id:
            continue
        presets[preset_id] = data
    return presets


SHADER_PRESETS = load_shader_presets()
