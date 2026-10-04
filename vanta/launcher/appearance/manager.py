"""Select one original resource pack per profile, and store a shader preset.

Selecting a pack copies that pack into the profile's resourcepacks directory.
Shader presets are descriptions. If Iris is not installed, the choice is saved
and the status is NOT_APPLIED. Vanta never reports a shader as running.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from vanta.launcher.appearance.catalog import (
    PACKS,
    PACKS_BY_ID,
    SHADER_PRESETS,
    pack_source,
)
from vanta.launcher.mods.manager import profile_slug
from vanta.shared.utilities.jsonio import read_json, write_json

STATUS_OFF = "OFF"
STATUS_SELECTED = "SELECTED"
STATUS_NOT_APPLIED = "NOT_APPLIED"

BOOL_ERROR = "That option must be true or false."
PACK_ERROR = "Unknown resource pack. Choose Vanta Vanilla+, Vanta Cartoon, or off."
PRESET_ERROR = "Unknown shader preset. Choose Vanta Smooth, Vanta Cinematic, or off."
CHOICE_ERROR = "Name the pack or preset to store."
FIELD_ERROR = "That field is not part of this request."
CONTROLLER_ERROR = "Controller settings are not accepted on this request."
THIRD_PARTY_ERROR = "Vanta does not bundle or enable third-party shader packs."
NO_LOADER_WARNING = (
    "No shader loader is installed. Iris was not found for this profile. "
    "The preset was stored and is not running."
)
LOADER_PRESENT_WARNING = (
    "Iris was found in this profile's mods folder, but Vanta did not start it. "
    "The preset was stored and is not running."
)

_OFF = {"off", "none"}
_ALLOWED_KEYS = {"profilename", "profile", "id", "preset", "pack", "enabled"}
_CONTROLLER_KEYS = {"controller", "controllers"}
_THIRD_PARTY_KEYS = {
    "complementary",
    "complementaryshaders",
    "bsl",
    "shaderpack",
    "shaderpacks",
}


class AppearanceManager:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def manifest_path(self, profile_name: str) -> Path:
        return self.data_dir / "profiles" / profile_slug(profile_name) / "appearance.json"

    def resourcepacks_dir(self, profile_name: str) -> Path:
        return self.data_dir / "profiles" / profile_slug(profile_name) / "resourcepacks"

    def state(self, profile_name: str) -> dict[str, Any]:
        stored = self._read(profile_name)
        return self._public(profile_name, stored)

    def select_pack(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._reject(payload)
        choice = self._choice(payload)
        if choice is None:
            raise ValueError(CHOICE_ERROR)
        stored = self._read(profile_name)
        if choice in _OFF:
            self._clear_packs(profile_name)
            stored["resourcePackId"] = None
            self._write(profile_name, stored)
            return self._public(profile_name, stored)
        spec = PACKS_BY_ID.get(choice)
        if spec is None:
            raise ValueError(PACK_ERROR)
        source = pack_source(spec.id)
        if not (source / "pack.mcmeta").is_file():
            raise ValueError(f"The {spec.name} pack folder is missing pack.mcmeta.")
        self._copy_pack(profile_name, spec.id, source)
        stored["resourcePackId"] = spec.id
        self._write(profile_name, stored)
        return self._public(profile_name, stored)

    def select_shader(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._reject(payload)
        choice = self._choice(payload)
        if choice is None:
            raise ValueError(CHOICE_ERROR)
        stored = self._read(profile_name)
        if choice in _OFF:
            stored["shaderPresetId"] = None
            self._write(profile_name, stored)
            return self._public(profile_name, stored)
        if choice not in SHADER_PRESETS:
            if _normalized(choice) in _THIRD_PARTY_KEYS:
                raise ValueError(THIRD_PARTY_ERROR)
            raise ValueError(PRESET_ERROR)
        stored["shaderPresetId"] = choice
        self._write(profile_name, stored)
        return self._public(profile_name, stored)

    def _copy_pack(self, profile_name: str, pack_id: str, source: Path) -> None:
        root = self.resourcepacks_dir(profile_name)
        root.mkdir(parents=True, exist_ok=True)
        for spec in PACKS:
            self._remove_path(root / spec.id)
        destination = root / pack_id
        shutil.copytree(source, destination)

    def _clear_packs(self, profile_name: str) -> None:
        root = self.resourcepacks_dir(profile_name)
        if not root.exists():
            return
        for spec in PACKS:
            self._remove_path(root / spec.id)

    def _remove_path(self, path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def loader_installed(self, profile_name: str) -> bool:
        """True only when this profile's mods folder has an Iris-named jar.

        A hit still does not mean a shader is running.
        """
        mods = self.data_dir / "profiles" / profile_slug(profile_name) / "mods"
        if not mods.is_dir():
            return False
        for path in mods.iterdir():
            if not path.is_file():
                continue
            name = path.name.lower()
            if "iris" not in name:
                continue
            if name.endswith(".jar") or name.endswith(".jar.disabled"):
                return True
        return False

    def _public(self, profile_name: str, stored: dict[str, Any]) -> dict[str, Any]:
        pack_id = stored.get("resourcePackId")
        spec = PACKS_BY_ID.get(pack_id) if isinstance(pack_id, str) else None
        copied = False
        installed = None
        if spec is not None:
            installed_path = self.resourcepacks_dir(profile_name) / spec.id
            copied = (installed_path / "pack.mcmeta").is_file()
            installed = str(installed_path) if copied else None
        pack_body = {
            "status": STATUS_SELECTED if spec is not None else STATUS_OFF,
            "id": spec.id if spec is not None else None,
            "name": spec.name if spec is not None else None,
            "summary": spec.summary if spec is not None else None,
            "copied": copied,
            "path": installed,
            "packComplete": False,
            "testedInMinecraft": False,
        }
        preset_id = stored.get("shaderPresetId")
        preset = SHADER_PRESETS.get(preset_id) if isinstance(preset_id, str) else None
        loader = self.loader_installed(profile_name)
        if preset is None:
            shader_body = {
                "status": STATUS_OFF,
                "id": None,
                "name": None,
                "goal": None,
                "performanceWarning": None,
                "enabled": False,
                "loaderInstalled": loader,
                "warning": None,
                "applied": False,
                "appliedInGame": False,
                "shaderRunning": False,
            }
            warning = None
        else:
            warning = LOADER_PRESENT_WARNING if loader else NO_LOADER_WARNING
            shader_body = {
                "status": STATUS_NOT_APPLIED,
                "id": preset.get("id"),
                "name": preset.get("name"),
                "goal": preset.get("goal"),
                "performanceWarning": preset.get("performanceWarning"),
                "enabled": True,
                "loaderInstalled": loader,
                "warning": warning,
                "applied": False,
                "appliedInGame": False,
                "shaderRunning": False,
            }
        return {
            "ok": True,
            "profileName": profile_name,
            "resourcePack": pack_body,
            "packs": [
                {"id": item.id, "name": item.name, "summary": item.summary}
                for item in PACKS
            ],
            "shader": shader_body,
            "shaderPresets": [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "goal": item.get("goal"),
                    "performanceWarning": item.get("performanceWarning"),
                    "bundlesThirdPartyShader": False,
                }
                for item in SHADER_PRESETS.values()
            ],
            "shaderRunning": False,
            "applied": False,
            "warning": warning,
            "packComplete": False,
            "testedInMinecraft": False,
            "minecraftLaunched": False,
            "controllerSupported": False,
        }

    def _read(self, profile_name: str) -> dict[str, Any]:
        path = self.manifest_path(profile_name)
        if not path.is_file():
            return {"resourcePackId": None, "shaderPresetId": None}
        data = read_json(path, {})
        if not isinstance(data, dict):
            return {"resourcePackId": None, "shaderPresetId": None}
        pack_id = data.get("resourcePackId")
        preset_id = data.get("shaderPresetId")
        if pack_id not in PACKS_BY_ID:
            pack_id = None
        if preset_id not in SHADER_PRESETS:
            preset_id = None
        return {"resourcePackId": pack_id, "shaderPresetId": preset_id}

    def _write(self, profile_name: str, stored: dict[str, Any]) -> None:
        write_json(
            self.manifest_path(profile_name),
            {
                "version": 1,
                "resourcePackId": stored.get("resourcePackId"),
                "shaderPresetId": stored.get("shaderPresetId"),
            },
        )

    def _choice(self, payload: dict[str, Any]) -> str | None:
        if "enabled" in payload and not isinstance(payload.get("enabled"), bool):
            raise ValueError(BOOL_ERROR)
        raw = None
        for key in ("id", "preset", "pack"):
            if key in payload and payload.get(key) is not None:
                raw = payload.get(key)
                break
        if raw is None:
            return None
        if not isinstance(raw, str):
            raise ValueError(CHOICE_ERROR)
        text = raw.strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered in _OFF:
            return lowered
        return text

    def _reject(self, payload: dict[str, Any]) -> None:
        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized = _normalized(str(key))
                    if normalized in _CONTROLLER_KEYS:
                        raise ValueError(CONTROLLER_ERROR)
                    if normalized in _THIRD_PARTY_KEYS:
                        raise ValueError(THIRD_PARTY_ERROR)
                    if normalized not in _ALLOWED_KEYS:
                        raise ValueError(FIELD_ERROR)
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
            elif isinstance(value, str) and _normalized(value) in _THIRD_PARTY_KEYS:
                raise ValueError(THIRD_PARTY_ERROR)

        walk(payload)


def _normalized(value: str) -> str:
    return value.strip().lower().replace("_", "").replace("-", "").replace(" ", "")
