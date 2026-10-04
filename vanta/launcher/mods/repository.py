"""Abstract mod repository. Implementations talk to a specific public API."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from vanta.launcher.mods.models import ModProject, ModVersion


class ModRepositoryError(Exception):
    """Raised when a repository call fails for a clear, user-facing reason."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ModRepository(ABC):
    """Interface for searching and fetching mods. UI must not call APIs directly."""

    name: str = "abstract"

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        game_version: str,
        loader: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ModProject]:
        raise NotImplementedError

    @abstractmethod
    def get_project(self, project_id_or_slug: str) -> ModProject:
        raise NotImplementedError

    @abstractmethod
    def get_versions(
        self,
        project_id_or_slug: str,
        *,
        game_version: str | None = None,
        loader: str | None = None,
    ) -> list[ModVersion]:
        raise NotImplementedError

    @abstractmethod
    def get_version(self, version_id: str) -> ModVersion:
        raise NotImplementedError

    @abstractmethod
    def download_file(self, url: str) -> bytes:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"name": self.name}
