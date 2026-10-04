"""Abstract skin repository. The UI must not call a skin host directly."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from vanta.launcher.skins.models import SkinHit


class SkinRepositoryError(Exception):
    """A lookup failed. Callers must not treat this as an empty result list."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SkinRepository(ABC):
    name: str = "abstract"

    @abstractmethod
    def search(self, query: str, *, limit: int = 5) -> list[SkinHit]:
        raise NotImplementedError

    @abstractmethod
    def fetch_texture(self, skin_id: str) -> bytes:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        return {"name": self.name}
