"""Version registry. The UI must consume this list rather than hard-coding one version."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).with_name("registry.json")
REQUIRED_FIELDS = (
    "versionNumber",
    "loader",
    "mappings",
    "gameDirectory",
    "compatibleMods",
    "compatibleLibraries",
)


def _raw_entries() -> list[dict[str, Any]]:
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Version registry must be a list.")
    return [dict(item) for item in data]


def list_versions(game_directory: str) -> list[dict[str, Any]]:
    base = Path(game_directory)
    versions: list[dict[str, Any]] = []
    for row in _raw_entries():
        item = dict(row)
        number = str(item.get("versionNumber") or "")
        loader = str(item.get("loader") or "")
        item["gameDirectory"] = str(base / f"{number}-{loader}")
        # 0.1 never claims mod compatibility. An empty list is intentional.
        item["compatibleMods"] = list(item.get("compatibleMods") or [])
        item["compatibleLibraries"] = list(item.get("compatibleLibraries") or [])
        versions.append(item)
    return versions


def get_version(version_number: str, loader: str, game_directory: str) -> dict[str, Any] | None:
    for item in list_versions(game_directory):
        if item["versionNumber"] == version_number and item["loader"] == loader:
            return item
    return None


def supported_versions(game_directory: str) -> list[dict[str, Any]]:
    return [item for item in list_versions(game_directory) if item.get("supported") is True]
