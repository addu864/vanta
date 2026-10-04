"""Optional per-profile utility mods.

Only three utilities exist: Simple Voice Chat, Xaero's Minimap, and Xaero's
World Map. Each is toggled on its own. Enabling asks Modrinth for a version
that lists Minecraft 1.21.11 and Fabric. A search hit is not enough. No jar
is vendored, and nothing here launches Minecraft.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vanta.launcher.mods.compatibility import is_compatible
from vanta.launcher.mods.manager import profile_slug
from vanta.launcher.mods.models import TARGET_GAME_VERSION, TARGET_LOADER, ModVersion
from vanta.launcher.mods.repository import ModRepository, ModRepositoryError
from vanta.shared.utilities.jsonio import read_json, write_json

STATUS_OFF = "OFF"
STATUS_INCOMPATIBLE = "INCOMPATIBLE"
STATUS_ENABLED = "ENABLED"
STATUSES = (STATUS_OFF, STATUS_INCOMPATIBLE, STATUS_ENABLED)

UTILITY_NOTE = (
    "Optional and per profile. A toggle turns on only after a Modrinth version "
    "lists Minecraft 1.21.11 and Fabric. Search hits are not enough. No jar is "
    "vendored. Minecraft was not launched, and this does not mean the mod works in game."
)
BOOL_ERROR = "That option must be true or false."
UNKNOWN_ERROR = (
    "Unknown utility. Choose Simple Voice Chat, Xaero's Minimap, or Xaero's World Map."
)
MISSING_TOGGLE_ERROR = "Say whether the utility is on or off."
SCOPE_ERROR = (
    "The utilities toggle does not enable resource packs, shader packs, or controller support."
)

_OUT_OF_SCOPE = {
    "resourcepack",
    "resourcepacks",
    "shader",
    "shaders",
    "shaderpack",
    "shaderpacks",
    "controller",
    "controllers",
}


@dataclass(frozen=True)
class UtilitySpec:
    id: str
    slug: str
    title: str
    summary: str


# Slugs were checked against the public Modrinth API on 3 Oct 2026.
# xaerominimap and xaeroworldmap 404. The projects are xaeros-minimap and
# xaeros-world-map. No other voice, map, or minimap mod is offered.
UTILITY_SPECS: tuple[UtilitySpec, ...] = (
    UtilitySpec(
        id="simple-voice-chat",
        slug="simple-voice-chat",
        title="Simple Voice Chat",
        summary="Proximity voice. Optional. Not verified in a running game.",
    ),
    UtilitySpec(
        id="xaeros-minimap",
        slug="xaeros-minimap",
        title="Xaero's Minimap",
        summary="Minimap. Optional. Not verified in a running game.",
    ),
    UtilitySpec(
        id="xaeros-world-map",
        slug="xaeros-world-map",
        title="Xaero's World Map",
        summary="World map. Optional. Not verified in a running game.",
    ),
)

_BY_ID = {item.id: item for item in UTILITY_SPECS}


def utility_spec(utility_id: str) -> UtilitySpec | None:
    return _BY_ID.get((utility_id or "").strip())


class UtilityManager:
    """Resolve and remember utility toggles. Download is a separate step and is not called."""

    def __init__(
        self,
        data_dir: Path,
        repository: ModRepository,
        *,
        game_version: str = TARGET_GAME_VERSION,
        loader: str = TARGET_LOADER,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.repository = repository
        self.game_version = game_version
        self.loader = loader

    def manifest_path(self, profile_name: str) -> Path:
        return self.data_dir / "profiles" / profile_slug(profile_name) / "utilities.json"

    def state(self, profile_name: str) -> dict[str, Any]:
        stored = self._read(profile_name)
        return self._payload(profile_name, stored, ok=True)

    def set_enabled(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._reject_scope(payload)
        utility_id = payload.get("id") if "id" in payload else payload.get("utility")
        if not isinstance(utility_id, str) or not utility_id.strip():
            raise ValueError(UNKNOWN_ERROR)
        spec = utility_spec(utility_id)
        if spec is None:
            raise ValueError(UNKNOWN_ERROR)
        if "enabled" not in payload:
            raise ValueError(MISSING_TOGGLE_ERROR)
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError(BOOL_ERROR)

        stored = self._read(profile_name)
        if not enabled:
            stored[spec.id] = self._off_record(spec)
            self._write(profile_name, stored)
            body = self._payload(profile_name, stored, ok=True)
            body["changed"] = spec.id
            return body

        try:
            resolved = self.resolve_compatible(spec)
        except ModRepositoryError as exc:
            body = self._payload(profile_name, stored, ok=False)
            body["error"] = f"{exc.message} The toggle was not changed."
            body["changed"] = spec.id
            body["downloaded"] = False
            body["jarWritten"] = False
            return body

        if resolved is None:
            stored[spec.id] = self._incompatible_record(
                spec,
                (
                    f"INCOMPATIBLE: no Modrinth version of '{spec.title}' lists "
                    f"Minecraft {self.game_version} and loader {self.loader}. "
                    "The toggle stays off. Nothing was downloaded."
                ),
            )
            self._write(profile_name, stored)
            body = self._payload(profile_name, stored, ok=False)
            body["error"] = stored[spec.id]["error"]
            body["changed"] = spec.id
            return body

        version, filename = resolved
        stored[spec.id] = {
            "id": spec.id,
            "slug": spec.slug,
            "projectId": version.project_id,
            "enabled": True,
            "status": STATUS_ENABLED,
            "versionId": version.id,
            "versionNumber": version.version_number,
            "filename": filename,
            "gameVersions": list(version.game_versions),
            "loaders": list(version.loaders),
            "error": None,
            "downloaded": False,
            "jarWritten": False,
        }
        self._write(profile_name, stored)
        body = self._payload(profile_name, stored, ok=True)
        body["changed"] = spec.id
        return body

    def resolve_compatible(self, spec: UtilitySpec) -> tuple[ModVersion, str] | None:
        """Return a version that itself lists the target game and loader, plus an https file.

        Does not call search and does not download.
        """
        project = self.repository.get_project(spec.slug)
        versions = self.repository.get_versions(
            project.id or project.slug,
            game_version=self.game_version,
            loader=self.loader,
        )
        for version in versions:
            if not is_compatible(version, game_version=self.game_version, loader=self.loader):
                continue
            filename = self._https_filename(version)
            if filename is None:
                continue
            if not version.project_id:
                version.project_id = project.id
            return version, filename
        return None

    def _https_filename(self, version: ModVersion) -> str | None:
        files = [item for item in version.files if item.url.startswith("https://") and item.filename]
        if not files:
            return None
        primary = next((item for item in files if item.primary), files[0])
        return primary.filename

    def _off_record(self, spec: UtilitySpec) -> dict[str, Any]:
        return {
            "id": spec.id,
            "slug": spec.slug,
            "projectId": None,
            "enabled": False,
            "status": STATUS_OFF,
            "versionId": None,
            "versionNumber": None,
            "filename": None,
            "gameVersions": [],
            "loaders": [],
            "error": None,
            "downloaded": False,
            "jarWritten": False,
        }

    def _incompatible_record(self, spec: UtilitySpec, message: str) -> dict[str, Any]:
        record = self._off_record(spec)
        record["status"] = STATUS_INCOMPATIBLE
        record["error"] = message
        return record

    def _read(self, profile_name: str) -> dict[str, dict[str, Any]]:
        data = read_json(self.manifest_path(profile_name), {"utilities": {}})
        raw = data.get("utilities") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return {}
        cleaned: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            if key in _BY_ID and isinstance(value, dict):
                cleaned[key] = value
        return cleaned

    def _write(self, profile_name: str, items: dict[str, dict[str, Any]]) -> None:
        write_json(
            self.manifest_path(profile_name),
            {"version": 1, "utilities": items},
        )

    def _public_item(self, spec: UtilitySpec, raw: dict[str, Any] | None) -> dict[str, Any]:
        record = raw or {}
        status = record.get("status")
        if status not in STATUSES:
            status = STATUS_OFF
        version_id = record.get("versionId") if isinstance(record.get("versionId"), str) else None
        enabled = status == STATUS_ENABLED and record.get("enabled") is True and bool(version_id)
        if not enabled:
            if status == STATUS_ENABLED:
                status = STATUS_OFF
            enabled = False
        if status == STATUS_OFF:
            enabled = False
        game_versions = record.get("gameVersions") if isinstance(record.get("gameVersions"), list) else []
        loaders = record.get("loaders") if isinstance(record.get("loaders"), list) else []
        error = record.get("error") if isinstance(record.get("error"), str) else None
        return {
            "id": spec.id,
            "slug": spec.slug,
            "title": spec.title,
            "summary": spec.summary,
            "enabled": enabled,
            "status": status,
            "projectId": record.get("projectId") if isinstance(record.get("projectId"), str) else None,
            "versionId": version_id if enabled else (None if status != STATUS_INCOMPATIBLE else None),
            "versionNumber": record.get("versionNumber") if enabled and isinstance(record.get("versionNumber"), str) else None,
            "filename": record.get("filename") if enabled and isinstance(record.get("filename"), str) else None,
            "gameVersions": [str(item) for item in game_versions] if enabled else [],
            "loaders": [str(item) for item in loaders] if enabled else [],
            "error": error if status == STATUS_INCOMPATIBLE else None,
            "downloaded": False,
            "jarWritten": False,
            "worksInGame": False,
            "minecraftLaunched": False,
        }

    def _payload(self, profile_name: str, stored: dict[str, dict[str, Any]], *, ok: bool) -> dict[str, Any]:
        return {
            "ok": ok,
            "profileName": profile_name,
            "note": UTILITY_NOTE,
            "gameVersion": self.game_version,
            "loader": self.loader,
            "searchUsed": False,
            "downloaded": False,
            "jarWritten": False,
            "worksInGame": False,
            "minecraftLaunched": False,
            "utilities": [self._public_item(spec, stored.get(spec.id)) for spec in UTILITY_SPECS],
        }

    def _reject_scope(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError(UNKNOWN_ERROR)
        for key in payload:
            folded = "".join(ch for ch in str(key).lower() if ch.isalnum())
            if folded in _OUT_OF_SCOPE:
                raise ValueError(SCOPE_ERROR)
