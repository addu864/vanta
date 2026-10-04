"""Skin records shared by repositories and the local library."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LOCAL_APPLY_NOTE = (
    "Saved on this LOCAL TEST ACCOUNT only. "
    "Online upload needs a Microsoft account, which is not configured. "
    "This skin was not uploaded to Microsoft."
)

MODEL_STEVE = "steve"
MODEL_ALEX = "alex"
MODELS = {MODEL_STEVE, MODEL_ALEX}


@dataclass
class SkinHit:
    """One skin returned by a repository. No local favorite state here."""

    id: str
    name: str
    username: str | None
    source: str
    model: str
    remote_texture: str | None = None
    category: str | None = None
    source_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "username": self.username,
            "source": self.source,
            "model": self.model if self.model in MODELS else MODEL_STEVE,
            "remoteTexture": self.remote_texture,
        }
        if self.category:
            data["category"] = self.category
        if self.source_note:
            data["sourceNote"] = self.source_note
        return data
