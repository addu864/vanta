"""Launcher profiles. Built-ins are honest about mods that are not installed."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from vanta.launcher.mods.intents import performance_mods, sanitize_mod_intent
from vanta.shared.utilities.jsonio import read_json, write_json


def _valid_profile_name(name: str) -> bool:
    if not 1 <= len(name) <= 32:
        return False
    return all(ch.isprintable() for ch in name)


PERFORMANCE = "Vanta Performance"
VANILLA_PLUS = "Vanta Vanilla+"


def _builtin_profiles(default_ram: int) -> list[dict[str, Any]]:
    return [
        {
            "name": PERFORMANCE,
            "builtin": True,
            "intent": "MAX FPS",
            "shaders": False,
            "ramGb": default_ram,
            "modsIntent": performance_mods(),
            "settings": {
                "fpsIntent": "max",
                "shaders": False,
                "shaderPacks": "none",
            },
            "notes": (
                "MAX FPS intent, no shaders. Listed mods are planned, not installed, "
                "and have not been compatibility-tested."
            ),
        },
        {
            "name": VANILLA_PLUS,
            "builtin": True,
            "intent": "balanced",
            "shaders": False,
            "ramGb": default_ram,
            "modsIntent": [],
            "settings": {
                "fpsIntent": "balanced",
                "shaders": False,
                "shaderPacks": "none",
            },
            "notes": "Balanced profile. No mods are installed in Vanta 0.1.",
        },
    ]


class ProfileStore:
    def __init__(self, directory: Path, default_ram: int = 4) -> None:
        self.directory = directory
        self.path = directory / "profiles.json"
        self.default_ram = default_ram
        self.directory.mkdir(parents=True, exist_ok=True)
        self._ensure()

    def list(self) -> list[dict[str, Any]]:
        self._ensure()
        data = read_json(self.path, {"profiles": []})
        profiles = data.get("profiles", []) if isinstance(data, dict) else []
        return [copy.deepcopy(item) for item in profiles if isinstance(item, dict)]

    def get(self, name: str | None) -> dict[str, Any] | None:
        if not name:
            return None
        for profile in self.list():
            if profile.get("name") == name:
                return profile
        return None

    def create(self, name: str, ram_gb: int) -> dict[str, Any]:
        cleaned = (name or "").strip()
        if not _valid_profile_name(cleaned):
            raise ValueError("Profile name must be 1–32 characters and must not contain control characters.")
        if self.get(cleaned) is not None:
            raise ValueError(f"A profile named {cleaned} already exists.")
        profile = {
            "name": cleaned,
            "builtin": False,
            "intent": "custom",
            "shaders": False,
            "ramGb": ram_gb,
            "modsIntent": [],
            "settings": {
                "fpsIntent": "custom",
                "shaders": False,
                "shaderPacks": "none",
            },
            "notes": "Custom profile. Vanta 0.1 does not install mods for it.",
        }
        profiles = self.list()
        profiles.append(profile)
        self._write(profiles)
        return copy.deepcopy(profile)

    def create_from_instance(self, name: str, ram_gb: int, instance_directory: str) -> dict[str, Any]:
        """Add a profile that points at an existing instance folder.

        The folder must already contain client.jar, or a 1.21.11-fabric child
        that does. Nothing is downloaded and nothing is copied.
        """
        folder = Path(str(instance_directory or "")).expanduser()
        if not folder.is_dir():
            raise ValueError("That folder does not exist.")
        try:
            resolved = folder.resolve()
        except OSError as exc:
            raise ValueError(f"That folder could not be opened: {exc}") from exc
        jar = resolved / "client.jar"
        nested = resolved / "1.21.11-fabric" / "client.jar"
        if not jar.is_file() and nested.is_file():
            resolved = nested.parent
            jar = nested
        if not jar.is_file():
            raise ValueError(
                "That folder is not a Minecraft instance. client.jar was not found in it "
                "or in a 1.21.11-fabric folder inside it. Nothing was added."
            )
        cleaned = (name or "").strip() or resolved.name
        if not _valid_profile_name(cleaned):
            raise ValueError("Profile name must be 1–32 characters and must not contain control characters.")
        if self.get(cleaned) is not None:
            raise ValueError(f"A profile named {cleaned} already exists.")
        profile = {
            "name": cleaned,
            "builtin": False,
            "intent": "instance",
            "shaders": False,
            "ramGb": ram_gb,
            "modsIntent": [],
            "instanceDirectory": str(resolved),
            "settings": {
                "fpsIntent": "custom",
                "shaders": False,
                "shaderPacks": "none",
            },
            "notes": f"Instance folder: {resolved}",
        }
        profiles = self.list()
        profiles.append(profile)
        self._write(profiles)
        return copy.deepcopy(profile)

    def set_ram(self, name: str, ram_gb: int) -> dict[str, Any]:
        profiles = self.list()
        found = None
        for profile in profiles:
            if profile.get("name") == name:
                profile["ramGb"] = ram_gb
                found = profile
                break
        if found is None:
            raise ValueError(f"No profile named {name}.")
        self._write(profiles)
        return copy.deepcopy(found)

    def _ensure(self) -> None:
        data = read_json(self.path, {"profiles": []})
        stored = data.get("profiles", []) if isinstance(data, dict) else []
        by_name = {item.get("name"): item for item in stored if isinstance(item, dict)}
        merged: list[dict[str, Any]] = []
        for builtin in _builtin_profiles(self.default_ram):
            existing = by_name.pop(builtin["name"], None)
            if isinstance(existing, dict) and isinstance(existing.get("ramGb"), int):
                builtin["ramGb"] = existing["ramGb"]
            merged.append(builtin)
        for name, existing in by_name.items():
            if not name or existing.get("builtin") is True:
                continue
            mods = []
            for raw in existing.get("modsIntent") or []:
                cleaned = sanitize_mod_intent(raw)
                if cleaned is not None:
                    mods.append(cleaned)
            existing["modsIntent"] = mods
            existing["builtin"] = False
            existing["shaders"] = False
            raw_dir = existing.get("instanceDirectory")
            if isinstance(raw_dir, str) and raw_dir.strip():
                existing["instanceDirectory"] = raw_dir.strip()
            else:
                existing.pop("instanceDirectory", None)
            merged.append(existing)
        self._write(merged)

    def _write(self, profiles: list[dict[str, Any]]) -> None:
        write_json(self.path, {"profiles": profiles})
