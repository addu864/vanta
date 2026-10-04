"""Shared mod models used by repositories and the install manager."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


TARGET_GAME_VERSION = "1.21.11"
TARGET_LOADER = "fabric"

STATE_INSTALL = "INSTALL"
STATE_INSTALLED = "INSTALLED"
STATE_UPDATE = "UPDATE"
STATE_INCOMPATIBLE = "INCOMPATIBLE"
STATE_DEPENDENCY_REQUIRED = "DEPENDENCY REQUIRED"


@dataclass
class ModFile:
    filename: str
    url: str
    size: int = 0
    primary: bool = True
    hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModDependency:
    project_id: str | None
    version_id: str | None
    dependency_type: str
    file_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "projectId": self.project_id,
            "versionId": self.version_id,
            "dependencyType": self.dependency_type,
            "fileName": self.file_name,
        }


@dataclass
class ModVersion:
    id: str
    project_id: str
    name: str
    version_number: str
    game_versions: list[str]
    loaders: list[str]
    files: list[ModFile]
    dependencies: list[ModDependency] = field(default_factory=list)
    version_type: str = "release"
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "projectId": self.project_id,
            "name": self.name,
            "versionNumber": self.version_number,
            "gameVersions": list(self.game_versions),
            "loaders": list(self.loaders),
            "files": [item.to_dict() for item in self.files],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "versionType": self.version_type,
            "source": self.source,
        }


@dataclass
class ModProject:
    id: str
    slug: str
    title: str
    description: str
    author: str
    icon_url: str | None
    project_type: str
    game_versions: list[str]
    loaders: list[str]
    source: str = "unknown"
    download_count: int = 0
    categories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "author": self.author,
            "iconUrl": self.icon_url,
            "projectType": self.project_type,
            "gameVersions": list(self.game_versions),
            "loaders": list(self.loaders),
            "source": self.source,
            "downloadCount": self.download_count,
            "categories": list(self.categories),
        }


@dataclass
class InstalledMod:
    project_id: str
    slug: str
    title: str
    version_id: str
    version_number: str
    filename: str
    enabled: bool
    source: str
    author: str = ""
    icon_url: str | None = None
    game_versions: list[str] = field(default_factory=list)
    loaders: list[str] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projectId": self.project_id,
            "slug": self.slug,
            "title": self.title,
            "versionId": self.version_id,
            "versionNumber": self.version_number,
            "filename": self.filename,
            "enabled": self.enabled,
            "source": self.source,
            "author": self.author,
            "iconUrl": self.icon_url,
            "gameVersions": list(self.game_versions),
            "loaders": list(self.loaders),
            "dependencies": list(self.dependencies),
        }
