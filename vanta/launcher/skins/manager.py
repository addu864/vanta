"""Search, import, favorite, and locally apply skins. No Microsoft upload."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from vanta.launcher.skins.library import SkinLibrary
from vanta.launcher.skins.models import LOCAL_APPLY_NOTE, MODEL_STEVE, MODELS, SkinHit
from vanta.launcher.skins.pngskin import inspect_skin_png
from vanta.launcher.skins.repository import SkinRepository, SkinRepositoryError


class SkinManager:
    def __init__(self, data_dir: Path, repository: SkinRepository) -> None:
        self.data_dir = data_dir
        self.repository = repository
        self.library = SkinLibrary(data_dir)

    def search(self, query: str, *, skin_filter: str = "all", limit: int = 5) -> dict[str, Any]:
        filt = (skin_filter or "all").strip().lower()
        if filt == "favorites":
            return self._filter_favorites(query)
        fetch_limit = limit
        if filt not in {"all", "favorites", ""} and filt not in MODELS:
            fetch_limit = max(limit, 500)
        try:
            hits = self.repository.search(query, limit=fetch_limit)
        except SkinRepositoryError as exc:
            return {"ok": False, "error": exc.message}
        records = [self._store_hit(hit) for hit in hits]
        if filt in MODELS:
            records = [item for item in records if item.get("model") == filt]
        elif filt not in {"all", "favorites", ""}:
            wanted = filt
            records = [
                item
                for item in records
                if str(item.get("category") or "").strip().lower() == wanted
            ]
        message = None
        if not records:
            if filt in MODELS:
                message = f"No {filt} skins matched that lookup."
            elif self.repository.name == "vanta-library":
                if filt not in {"all", "favorites", ""} and filt not in MODELS:
                    message = "No skins in that category matched."
                else:
                    message = "No skins in Vanta's library matched that name."
            else:
                message = "No skin was found for that username on the public skin API."
        return {
            "ok": True,
            "query": query,
            "source": self.repository.name,
            "filter": filt,
            "results": records,
            "message": message,
        }

    def _filter_favorites(self, query: str) -> dict[str, Any]:
        needle = (query or "").strip().lower()
        items = self.library.favorites()
        if needle:
            items = [
                item
                for item in items
                if needle in str(item.get("name") or "").lower()
                or needle in str(item.get("username") or "").lower()
            ]
        message = None if items else "No favorite skins match."
        return {
            "ok": True,
            "query": query,
            "source": "local",
            "filter": "favorites",
            "results": items,
            "message": message,
        }

    def _store_hit(self, hit: SkinHit) -> dict[str, Any]:
        return self._public(self.library.upsert(self._hit_record(hit)))

    def set_favorite(self, skin_id: str, favorite: bool) -> dict[str, Any]:
        try:
            record = self.library.set_favorite(skin_id, bool(favorite))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skin": self._public(record)}

    def set_model(self, skin_id: str, model: str) -> dict[str, Any]:
        try:
            record = self.library.set_model(skin_id, str(model or "").strip().lower())
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skin": self._public(record)}

    def view(self, skin_id: str) -> dict[str, Any]:
        try:
            record = self.library.remember(skin_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "skin": self._public(record)}

    def favorites(self) -> dict[str, Any]:
        return {"ok": True, "results": [self._public(item) for item in self.library.favorites()]}

    def recent(self) -> dict[str, Any]:
        return {"ok": True, "results": [self._public(item) for item in self.library.recent()]}

    def import_png(self, png: bytes, *, name: str, model: str) -> dict[str, Any]:
        try:
            width, height = inspect_skin_png(png)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        label = (name or "").strip() or "Imported skin"
        if len(label) > 64:
            return {"ok": False, "error": "Skin name must be 64 characters or fewer."}
        chosen = (model or MODEL_STEVE).strip().lower()
        if chosen not in MODELS:
            return {"ok": False, "error": "Skin model must be steve or alex."}
        record = self.library.write_import(
            png, name=label, model=chosen, width=width, height=height
        )
        return {"ok": True, "skin": self._public(record)}

    def texture(self, skin_id: str) -> tuple[bytes, dict[str, Any]] | dict[str, Any]:
        record = self.library.get_record(skin_id)
        if record is None:
            return {"ok": False, "error": "That skin is not in the local library yet. Search or import it first."}
        local = self.library.read_bytes(record)
        if local is not None:
            return local, record
        try:
            png = self.repository.fetch_texture(skin_id)
        except SkinRepositoryError as exc:
            return {"ok": False, "error": exc.message}
        try:
            width, height = inspect_skin_png(png)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        filename = _file_name(skin_id)
        path = self.library.files / filename
        path.write_bytes(png)
        record["file"] = f"skins/files/{filename}"
        record["width"] = width
        record["height"] = height
        stored = self.library.upsert(record)
        return png, stored

    def apply_local(self, account: dict[str, Any], skin_id: str, model: str | None = None) -> dict[str, Any]:
        if model:
            changed = self.set_model(skin_id, model)
            if not changed.get("ok"):
                return changed
        record = self.library.get_record(skin_id)
        if record is None:
            return {"ok": False, "error": "Search or import a skin before saving it."}
        loaded = self.texture(skin_id)
        if isinstance(loaded, dict):
            return loaded
        png, record = loaded
        chosen = str(record.get("model") or MODEL_STEVE)
        meta = {
            "accountId": account.get("id"),
            "accountLabel": account.get("label"),
            "username": account.get("username"),
            "skinId": skin_id,
            "model": chosen,
            "source": record.get("source"),
            "uploadedToMicrosoft": False,
            "note": LOCAL_APPLY_NOTE,
        }
        try:
            relative = self.library.write_account_skin(str(account["id"]), png, meta)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "uploadedToMicrosoft": False,
            "note": LOCAL_APPLY_NOTE,
            "skinFile": relative,
            "model": chosen,
            "skin": self._public(record),
        }

    def state(self) -> dict[str, Any]:
        catalog_fn = getattr(self.repository, "catalog", None)
        hits = catalog_fn() if callable(catalog_fn) else []
        # One locked write for the whole catalog. Per-hit upserts raced under
        # ThreadingHTTPServer and corrupted library.json on Windows.
        records = [self._hit_record(hit) for hit in hits]
        catalog = [self._public(item) for item in self.library.upsert_many(records)]
        return {
            "ok": True,
            "repository": self.repository.describe(),
            "catalog": catalog,
            "favorites": [self._public(item) for item in self.library.favorites()],
            "recent": [self._public(item) for item in self.library.recent()],
            "note": LOCAL_APPLY_NOTE,
            "preview": "2d-png",
            "previewNote": (
                "Preview is a 2D image of the skin PNG (flat front composite plus the texture). "
                "A rotating WebGL preview is not in this slice."
            ),
        }

    def _hit_record(self, hit: SkinHit) -> dict[str, Any]:
        existing = self.library.get_record(hit.id) or {}
        model = existing.get("model") or hit.model or MODEL_STEVE
        record = {
            "id": hit.id,
            "name": hit.name,
            "username": hit.username,
            "source": hit.source,
            "model": model if model in MODELS else MODEL_STEVE,
            "remoteTexture": hit.remote_texture,
            "previewPath": "/api/skins/texture?id=" + quote(hit.id, safe=""),
            "uploadedToMicrosoft": False,
        }
        if hit.category:
            record["category"] = hit.category
        elif existing.get("category"):
            record["category"] = existing["category"]
        if hit.source_note:
            record["sourceNote"] = hit.source_note
        elif existing.get("sourceNote"):
            record["sourceNote"] = existing["sourceNote"]
        if existing.get("file"):
            record["file"] = existing["file"]
        if existing.get("width"):
            record["width"] = existing["width"]
        if existing.get("height"):
            record["height"] = existing["height"]
        return record

    def _public(self, record: dict[str, Any]) -> dict[str, Any]:
        item = dict(record)
        item["uploadedToMicrosoft"] = False
        item["previewPath"] = "/api/skins/texture?id=" + quote(str(item.get("id") or ""), safe="")
        item.pop("remoteTexture", None)
        return item


def _file_name(skin_id: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in skin_id)
    if not cleaned:
        raise SkinRepositoryError("That skin id cannot be cached.")
    return cleaned + ".png"
