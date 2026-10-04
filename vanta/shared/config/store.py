"""Persisted settings. JVM arguments are stored and never executed."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from vanta.shared.utilities.jsonio import read_json, write_json

FORBIDDEN_KEYS = {
    "token",
    "tokens",
    "access_token",
    "refresh_token",
    "id_token",
    "accessToken",
    "refreshToken",
    "idToken",
    "client_secret",
    "clientSecret",
}


class ConfigStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "config.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            write_json(self.path, self.defaults())

    def defaults(self) -> dict[str, Any]:
        return {
            "language": "en",
            "theme": "dark",
            "defaultVersion": "1.21.11",
            "defaultLoader": "fabric",
            "defaultRamGb": 4,
            "gameDirectory": str(self.directory / "game"),
            "jvmArgs": "",
            "jvmArgsExecuted": False,
            "selectedAccountId": None,
            "selectedProfileName": "Vanta Performance",
            "milestone": "1.0",
        }

    def get(self) -> dict[str, Any]:
        stored = read_json(self.path, {})
        merged = self.defaults()
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in FORBIDDEN_KEYS:
                    continue
                merged[key] = value
        merged["jvmArgsExecuted"] = False
        merged["milestone"] = "1.0"
        return merged

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.get()
        for key, value in changes.items():
            if key in FORBIDDEN_KEYS or key == "jvmArgsExecuted":
                raise ValueError("Refusing to store tokens or to mark JVM arguments as executed.")
            current[key] = value
        current["jvmArgsExecuted"] = False
        write_json(self.path, current)
        return copy.deepcopy(current)
