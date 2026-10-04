"""Mod browser and per-profile install manager (Vanta 0.2).

Planned performance-mod intents from 0.1 remain in intents.py and are not
treated as installed files.
"""

from vanta.launcher.mods.manager import ModManager, profile_slug
from vanta.launcher.mods.models import (
    STATE_DEPENDENCY_REQUIRED,
    STATE_INCOMPATIBLE,
    STATE_INSTALL,
    STATE_INSTALLED,
    STATE_UPDATE,
    TARGET_GAME_VERSION,
    TARGET_LOADER,
)
from vanta.launcher.mods.modrinth import ModrinthRepository
from vanta.launcher.mods.repository import ModRepository, ModRepositoryError

__all__ = [
    "ModManager",
    "ModRepository",
    "ModRepositoryError",
    "ModrinthRepository",
    "TARGET_GAME_VERSION",
    "TARGET_LOADER",
    "STATE_INSTALL",
    "STATE_INSTALLED",
    "STATE_UPDATE",
    "STATE_INCOMPATIBLE",
    "STATE_DEPENDENCY_REQUIRED",
    "profile_slug",
]
