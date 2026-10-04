"""Compatibility checks for Minecraft version and loader."""

from __future__ import annotations

from vanta.launcher.mods.models import (
    TARGET_GAME_VERSION,
    TARGET_LOADER,
    ModVersion,
)


def is_compatible(
    version: ModVersion,
    *,
    game_version: str = TARGET_GAME_VERSION,
    loader: str = TARGET_LOADER,
) -> bool:
    """True only when this specific file/version lists both targets."""
    game_versions = {str(item).strip() for item in version.game_versions}
    loaders = {str(item).strip().lower() for item in version.loaders}
    return game_version in game_versions and loader.lower() in loaders


def compatibility_label(
    version: ModVersion | None,
    *,
    game_version: str = TARGET_GAME_VERSION,
    loader: str = TARGET_LOADER,
) -> str:
    if version is None:
        return "INCOMPATIBLE"
    if is_compatible(version, game_version=game_version, loader=loader):
        return "compatible"
    return "INCOMPATIBLE"
