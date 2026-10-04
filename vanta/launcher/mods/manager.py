"""Per-profile mod install, enable/disable, and remove."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vanta.launcher.mods.compatibility import is_compatible
from vanta.launcher.mods.models import (
    STATE_DEPENDENCY_REQUIRED,
    STATE_INCOMPATIBLE,
    STATE_INSTALL,
    STATE_INSTALLED,
    STATE_UPDATE,
    TARGET_GAME_VERSION,
    TARGET_LOADER,
    InstalledMod,
    ModProject,
    ModVersion,
)
from vanta.launcher.mods.repository import ModRepository, ModRepositoryError
from vanta.shared.utilities.jsonio import read_json, write_json

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def profile_slug(name: str) -> str:
    cleaned = SAFE_NAME.sub("-", (name or "").strip()).strip("-._")
    return cleaned[:64] or "profile"


class ModManager:
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
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def profiles_root(self) -> Path:
        root = self.data_dir / "profiles"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def profile_dir(self, profile_name: str) -> Path:
        path = self.profiles_root() / profile_slug(profile_name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def mods_dir(self, profile_name: str) -> Path:
        path = self.profile_dir(profile_name) / "mods"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def manifest_path(self, profile_name: str) -> Path:
        return self.profile_dir(profile_name) / "mods-manifest.json"

    def list_installed(self, profile_name: str) -> list[InstalledMod]:
        data = read_json(self.manifest_path(profile_name), {"mods": []})
        items = data.get("mods", []) if isinstance(data, dict) else []
        result: list[InstalledMod] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            result.append(self._from_manifest(raw))
        return result

    def get_installed(self, profile_name: str, project_id: str) -> InstalledMod | None:
        for item in self.list_installed(profile_name):
            if item.project_id == project_id:
                return item
        return None

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        projects = self.repository.search(
            query,
            game_version=self.game_version,
            loader=self.loader,
            limit=limit,
            offset=offset,
        )
        return [project.to_dict() for project in projects]

    def project_details(self, project_id_or_slug: str) -> dict[str, Any]:
        project = self.repository.get_project(project_id_or_slug)
        versions = self.repository.get_versions(
            project.id or project.slug,
            game_version=self.game_version,
            loader=self.loader,
        )
        compatible = [item for item in versions if is_compatible(item, game_version=self.game_version, loader=self.loader)]
        payload = project.to_dict()
        payload["compatibleVersions"] = [item.to_dict() for item in compatible]
        payload["hasCompatibleVersion"] = bool(compatible)
        return payload

    def resolve_install_state(
        self,
        profile_name: str,
        project: ModProject,
        *,
        compatible_version: ModVersion | None,
        required_missing: list[dict[str, Any]] | None = None,
    ) -> str:
        if compatible_version is None:
            return STATE_INCOMPATIBLE
        if required_missing:
            return STATE_DEPENDENCY_REQUIRED
        installed = self.get_installed(profile_name, project.id)
        if installed is None:
            return STATE_INSTALL
        if installed.version_id != compatible_version.id:
            return STATE_UPDATE
        return STATE_INSTALLED

    def card_for_project(
        self,
        profile_name: str,
        project: ModProject,
        *,
        versions: list[ModVersion] | None = None,
    ) -> dict[str, Any]:
        if versions is None:
            try:
                versions = self.repository.get_versions(
                    project.id or project.slug,
                    game_version=self.game_version,
                    loader=self.loader,
                )
            except ModRepositoryError:
                versions = []
        compatible = [
            item
            for item in versions
            if is_compatible(item, game_version=self.game_version, loader=self.loader)
        ]
        best = compatible[0] if compatible else None
        required_missing: list[dict[str, Any]] = []
        if best is not None:
            required_missing = self._unresolved_required(profile_name, best)
        state = self.resolve_install_state(
            profile_name,
            project,
            compatible_version=best,
            required_missing=None,
        )
        installed = self.get_installed(profile_name, project.id)
        card = project.to_dict()
        card["supportedMcVersion"] = self.game_version if best else (
            ", ".join(project.game_versions[:5]) if project.game_versions else "unknown"
        )
        card["loader"] = self.loader if best else (
            ", ".join(project.loaders[:5]) if project.loaders else "unknown"
        )
        card["installState"] = state
        card["compatibleVersion"] = best.to_dict() if best else None
        card["requiredDependencies"] = [
            dep.to_dict()
            for dep in (best.dependencies if best else [])
            if dep.dependency_type == "required"
        ]
        card["unresolvedRequiredDependencies"] = required_missing
        card["installed"] = installed.to_dict() if installed else None
        return card

    def install(
        self,
        profile_name: str,
        *,
        project_id: str | None = None,
        version_id: str | None = None,
        auto_deps: bool = True,
    ) -> dict[str, Any]:
        if not profile_name:
            return {"ok": False, "error": "A profile name is required to install mods.", "state": None}
        project_key = (project_id or "").strip()
        version_key = (version_id or "").strip()
        if not project_key and not version_key:
            return {"ok": False, "error": "Provide a project id or a version id to install.", "state": None}

        try:
            if version_key:
                version = self.repository.get_version(version_key)
                project = self.repository.get_project(version.project_id)
            else:
                project = self.repository.get_project(project_key)
                versions = self.repository.get_versions(
                    project.id,
                    game_version=self.game_version,
                    loader=self.loader,
                )
                compatible = [
                    item
                    for item in versions
                    if is_compatible(item, game_version=self.game_version, loader=self.loader)
                ]
                if not compatible:
                    return {
                        "ok": False,
                        "error": (
                            f"INCOMPATIBLE: no Modrinth version of '{project.title}' lists "
                            f"Minecraft {self.game_version} and loader {self.loader}."
                        ),
                        "state": STATE_INCOMPATIBLE,
                        "downloaded": False,
                    }
                version = compatible[0]
        except ModRepositoryError as exc:
            return {"ok": False, "error": exc.message, "state": None, "downloaded": False}

        if not is_compatible(version, game_version=self.game_version, loader=self.loader):
            return {
                "ok": False,
                "error": (
                    f"INCOMPATIBLE: version '{version.version_number}' does not list "
                    f"Minecraft {self.game_version} and Fabric. Nothing was downloaded."
                ),
                "state": STATE_INCOMPATIBLE,
                "downloaded": False,
            }

        unresolved = self._unresolved_required(profile_name, version)
        installed_deps: list[dict[str, Any]] = []
        if unresolved and auto_deps:
            still_missing: list[dict[str, Any]] = []
            for dep in unresolved:
                dep_result = self._try_auto_install_dependency(profile_name, dep)
                if dep_result.get("ok") is True:
                    installed_deps.append(dep_result)
                else:
                    still_missing.append({**dep, "reason": dep_result.get("error")})
            unresolved = still_missing

        if unresolved:
            names = ", ".join(
                item.get("projectId")
                or item.get("project_id")
                or item.get("versionId")
                or item.get("version_id")
                or "?"
                for item in unresolved
            )
            return {
                "ok": False,
                "error": (
                    f"DEPENDENCY REQUIRED: could not install required dependencies ({names}) "
                    f"with Minecraft {self.game_version} + Fabric. The main mod was not installed."
                ),
                "state": STATE_DEPENDENCY_REQUIRED,
                "downloaded": False,
                "unresolvedRequiredDependencies": unresolved,
                "installedDependencies": installed_deps,
            }

        primary = next((item for item in version.files if item.primary), None)
        if primary is None and version.files:
            primary = version.files[0]
        if primary is None:
            return {
                "ok": False,
                "error": "That Modrinth version has no downloadable file.",
                "state": None,
                "downloaded": False,
            }

        try:
            payload = self.repository.download_file(primary.url)
        except ModRepositoryError as exc:
            return {"ok": False, "error": exc.message, "state": None, "downloaded": False}

        if not payload:
            return {
                "ok": False,
                "error": "Downloaded mod file was empty. Nothing was saved.",
                "state": None,
                "downloaded": False,
            }

        mods_dir = self.mods_dir(profile_name)
        safe_name = SAFE_NAME.sub("_", primary.filename).strip("._") or f"{project.slug}.jar"
        if not safe_name.lower().endswith(".jar"):
            safe_name += ".jar"
        target = mods_dir / safe_name
        # Replace any prior file for this project.
        existing = self.get_installed(profile_name, project.id)
        if existing is not None:
            self._delete_file(mods_dir, existing.filename, existing.enabled)

        target.write_bytes(payload)
        record = InstalledMod(
            project_id=project.id,
            slug=project.slug,
            title=project.title,
            version_id=version.id,
            version_number=version.version_number,
            filename=safe_name,
            enabled=True,
            source=self.repository.name,
            author=project.author,
            icon_url=project.icon_url,
            game_versions=list(version.game_versions),
            loaders=list(version.loaders),
            dependencies=[dep.to_dict() for dep in version.dependencies],
        )
        self._upsert(profile_name, record)
        return {
            "ok": True,
            "state": STATE_INSTALLED,
            "downloaded": True,
            "mod": record.to_dict(),
            "path": str(target),
            "installedDependencies": installed_deps,
        }

    def remove(self, profile_name: str, project_id: str) -> dict[str, Any]:
        installed = self.get_installed(profile_name, project_id)
        if installed is None:
            return {"ok": False, "error": "That mod is not installed on this profile."}
        self._delete_file(self.mods_dir(profile_name), installed.filename, installed.enabled)
        remaining = [item for item in self.list_installed(profile_name) if item.project_id != project_id]
        self._write_manifest(profile_name, remaining)
        return {"ok": True, "removed": installed.to_dict()}

    def set_enabled(self, profile_name: str, project_id: str, enabled: bool) -> dict[str, Any]:
        installed = self.get_installed(profile_name, project_id)
        if installed is None:
            return {"ok": False, "error": "That mod is not installed on this profile."}
        mods_dir = self.mods_dir(profile_name)
        active = mods_dir / installed.filename
        disabled = mods_dir / (installed.filename + ".disabled")
        if enabled and not installed.enabled:
            if disabled.is_file():
                disabled.replace(active)
            elif not active.is_file():
                return {"ok": False, "error": f"Missing mod file for {installed.title}."}
            installed.enabled = True
        elif not enabled and installed.enabled:
            if active.is_file():
                active.replace(disabled)
            elif not disabled.is_file():
                return {"ok": False, "error": f"Missing mod file for {installed.title}."}
            installed.enabled = False
        self._upsert(profile_name, installed)
        return {"ok": True, "mod": installed.to_dict()}

    def installed_cards(self, profile_name: str) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for item in self.list_installed(profile_name):
            cards.append(
                {
                    **item.to_dict(),
                    "installState": STATE_INSTALLED if item.enabled else STATE_INSTALLED,
                    "supportedMcVersion": ", ".join(item.game_versions) or self.game_version,
                    "loader": ", ".join(item.loaders) or self.loader,
                    "description": "",
                    "enabled": item.enabled,
                }
            )
        return cards

    def _try_auto_install_dependency(self, profile_name: str, dep: dict[str, Any]) -> dict[str, Any]:
        project_id = dep.get("projectId") or dep.get("project_id")
        version_id = dep.get("versionId") or dep.get("version_id")
        if not project_id and not version_id:
            return {
                "ok": False,
                "error": "Dependency has no project id; cannot auto-install.",
            }
        if self.get_installed(profile_name, str(project_id or "")) is not None:
            return {"ok": True, "alreadyInstalled": True, "projectId": project_id}
        try:
            if version_id:
                version = self.repository.get_version(str(version_id))
                if not is_compatible(version, game_version=self.game_version, loader=self.loader):
                    return {
                        "ok": False,
                        "error": (
                            f"Dependency version {version_id} is not compatible with "
                            f"{self.game_version} + {self.loader}."
                        ),
                    }
                return self.install(
                    profile_name,
                    project_id=version.project_id,
                    version_id=version.id,
                    auto_deps=True,
                )
            project = self.repository.get_project(str(project_id))
            versions = self.repository.get_versions(
                project.id,
                game_version=self.game_version,
                loader=self.loader,
            )
            compatible = [
                item
                for item in versions
                if is_compatible(item, game_version=self.game_version, loader=self.loader)
            ]
            if not compatible:
                return {
                    "ok": False,
                    "error": (
                        f"No public {self.game_version}+{self.loader} version for dependency "
                        f"{project.title or project_id}."
                    ),
                }
            return self.install(
                profile_name,
                project_id=project.id,
                version_id=compatible[0].id,
                auto_deps=True,
            )
        except ModRepositoryError as exc:
            return {"ok": False, "error": exc.message}

    def _unresolved_required(self, profile_name: str, version: ModVersion) -> list[dict[str, Any]]:
        missing: list[dict[str, Any]] = []
        for dep in version.dependencies:
            if dep.dependency_type != "required":
                continue
            if dep.project_id and self.get_installed(profile_name, dep.project_id) is not None:
                continue
            missing.append(dep.to_dict())
        return missing

    def _upsert(self, profile_name: str, record: InstalledMod) -> None:
        items = [item for item in self.list_installed(profile_name) if item.project_id != record.project_id]
        items.append(record)
        self._write_manifest(profile_name, items)

    def _write_manifest(self, profile_name: str, items: list[InstalledMod]) -> None:
        write_json(
            self.manifest_path(profile_name),
            {"mods": [item.to_dict() for item in items]},
        )

    def _delete_file(self, mods_dir: Path, filename: str, enabled: bool) -> None:
        active = mods_dir / filename
        disabled = mods_dir / (filename + ".disabled")
        if active.is_file():
            active.unlink()
        if disabled.is_file():
            disabled.unlink()

    def _from_manifest(self, raw: dict[str, Any]) -> InstalledMod:
        return InstalledMod(
            project_id=str(raw.get("projectId") or ""),
            slug=str(raw.get("slug") or ""),
            title=str(raw.get("title") or ""),
            version_id=str(raw.get("versionId") or ""),
            version_number=str(raw.get("versionNumber") or ""),
            filename=str(raw.get("filename") or ""),
            enabled=bool(raw.get("enabled", True)),
            source=str(raw.get("source") or "unknown"),
            author=str(raw.get("author") or ""),
            icon_url=raw.get("iconUrl") if isinstance(raw.get("iconUrl"), str) else None,
            game_versions=[str(v) for v in (raw.get("gameVersions") or [])],
            loaders=[str(v) for v in (raw.get("loaders") or [])],
            dependencies=list(raw.get("dependencies") or []),
        )

