"""Vanta's own skin library. Files live in assets/skins. No Ely.by or NameMC calls."""

from __future__ import annotations

import json

from vanta.launcher.skins.models import MODEL_ALEX, MODEL_STEVE, SkinHit
from vanta.launcher.skins.pngskin import inspect_skin_png
from vanta.launcher.skins.repository import SkinRepository, SkinRepositoryError
from vanta.shared.config.paths import repo_root

# Original 64x64 drawings stored with the app. Steve and Alex here are arm
# models only. The pixels are Vanta's, not a copied game texture.
CATALOG = (
    {
        "id": "vanta-classic",
        "name": "Vanta Classic",
        "model": MODEL_STEVE,
        "file": "vanta-classic.png",
        "category": "Vanta",
        "sourceNote": "Vanta's own library.",
    },
    {
        "id": "vanta-slim",
        "name": "Vanta Slim",
        "model": MODEL_ALEX,
        "file": "vanta-slim.png",
        "category": "Vanta",
        "sourceNote": "Vanta's own library.",
    },
    {
        "id": "vanta-night",
        "name": "Vanta Night",
        "model": MODEL_STEVE,
        "file": "vanta-night.png",
        "category": "Vanta",
        "sourceNote": "Vanta's own library.",
    },
    {
        "id": "vanta-moss",
        "name": "Vanta Moss",
        "model": MODEL_ALEX,
        "file": "vanta-moss.png",
        "category": "Vanta",
        "sourceNote": "Vanta's own library.",
    },
    {
        "id": "vanta-ember",
        "name": "Vanta Ember",
        "model": MODEL_STEVE,
        "file": "vanta-ember.png",
        "category": "Vanta",
        "sourceNote": "Vanta's own library.",
    },
    {
        "id": "vanta-tide",
        "name": "Vanta Tide",
        "model": MODEL_ALEX,
        "file": "vanta-tide.png",
        "category": "Vanta",
        "sourceNote": "Vanta's own library.",
    },
)

SKINDEX_NOTE = "From Skindex (skindex.pro)."


class VantaSkinLibrary(SkinRepository):
    name = "vanta-library"

    def __init__(self, directory=None) -> None:
        self.directory = directory or (repo_root() / "assets" / "skins")

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": "Vanta skin library",
            "secretRequired": False,
            "catalogSearch": True,
            "offline": True,
            "lookup": "local-library",
            "limitation": (
                "These skins are Vanta's own library, stored in the app. "
                "Skindex skins were saved from public Skindex PNGs and stay on this computer. "
                "Ely.by and NameMC are not contacted. "
                "You can still import a PNG. "
                "Saving a skin stays on the LOCAL TEST ACCOUNT and is not a Microsoft upload."
            ),
        }

    def _items(self) -> list[dict]:
        items = [dict(item) for item in CATALOG]
        path = self.directory / "skindex-catalog.json"
        if not path.is_file():
            return items
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return items
        if not isinstance(loaded, list):
            return items
        known = {item["id"] for item in items}
        for item in loaded:
            if not isinstance(item, dict):
                continue
            skin_id = str(item.get("id") or "")
            filename = str(item.get("file") or "")
            if not skin_id or not filename or skin_id in known:
                continue
            if ".." in filename or filename.startswith("/"):
                continue
            model = str(item.get("model") or MODEL_STEVE)
            items.append(
                {
                    "id": skin_id,
                    "name": str(item.get("name") or skin_id)[:64],
                    "model": model if model in {MODEL_STEVE, MODEL_ALEX} else MODEL_STEVE,
                    "file": filename,
                    "category": str(item.get("category") or "Skindex"),
                    "sourceNote": str(item.get("sourceNote") or SKINDEX_NOTE),
                }
            )
            known.add(skin_id)
        return items

    def catalog(self) -> list[SkinHit]:
        hits: list[SkinHit] = []
        for item in self._items():
            hits.append(
                SkinHit(
                    id=item["id"],
                    name=item["name"],
                    username=None,
                    source=self.name,
                    model=item["model"],
                    remote_texture=None,
                    category=item.get("category"),
                    source_note=item.get("sourceNote"),
                )
            )
        return hits

    def search(self, query: str, *, limit: int = 24) -> list[SkinHit]:
        needle = (query or "").strip().lower()
        hits = self.catalog()
        if needle:
            hits = [
                hit
                for hit in hits
                if needle in hit.name.lower()
                or needle in hit.id.lower()
                or needle in (hit.category or "").lower()
            ]
        cap = max(1, min(int(limit or 24), 500))
        return hits[:cap]

    def fetch_texture(self, skin_id: str) -> bytes:
        wanted = next((item for item in self._items() if item["id"] == skin_id), None)
        if wanted is None:
            raise SkinRepositoryError("That skin is not in Vanta's library.")
        path = self.directory / wanted["file"]
        if not path.is_file():
            raise SkinRepositoryError("That Vanta library skin file is missing.")
        data = path.read_bytes()
        try:
            inspect_skin_png(data)
        except ValueError as exc:
            raise SkinRepositoryError(str(exc)) from exc
        return data
