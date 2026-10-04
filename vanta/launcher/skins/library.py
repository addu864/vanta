"""Favorites, recent skins, and imported files under the Vanta data directory."""

from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from vanta.launcher.skins.models import MODEL_STEVE, MODELS
from vanta.shared.utilities.jsonio import read_json, write_json

RECENT_LIMIT = 20


class SkinLibrary:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.root = directory / "skins"
        self.files = self.root / "files"
        self.local_accounts = self.root / "local-accounts"
        self.path = self.root / "library.json"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.files.mkdir(parents=True, exist_ok=True)
        self.local_accounts.mkdir(parents=True, exist_ok=True)
        if not self.path.is_file():
            write_json(self.path, self._empty())

    def _empty(self) -> dict[str, Any]:
        return {"records": {}, "favorites": [], "recent": []}

    def load(self) -> dict[str, Any]:
        data = read_json(self.path, self._empty())
        if not isinstance(data, dict):
            data = self._empty()
        records = data.get("records")
        favorites = data.get("favorites")
        recent = data.get("recent")
        return {
            "records": records if isinstance(records, dict) else {},
            "favorites": [item for item in favorites if isinstance(item, str)] if isinstance(favorites, list) else [],
            "recent": [item for item in recent if isinstance(item, dict)] if isinstance(recent, list) else [],
        }

    def save(self, data: dict[str, Any]) -> None:
        write_json(self.path, data)

    def get_record(self, skin_id: str) -> dict[str, Any] | None:
        record = self.load()["records"].get(skin_id)
        if not isinstance(record, dict):
            return None
        return copy.deepcopy(record)

    def upsert(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            return self._upsert_unlocked(record)

    def upsert_many(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge many skin rows with one load/save so catalog loads cannot race-corrupt library.json."""
        with self._lock:
            data = self.load()
            stored: list[dict[str, Any]] = []
            for record in records:
                stored.append(self._upsert_into(data, record))
            self.save(data)
            return [copy.deepcopy(item) for item in stored]

    def _upsert_unlocked(self, record: dict[str, Any]) -> dict[str, Any]:
        data = self.load()
        merged = self._upsert_into(data, record)
        self.save(data)
        return copy.deepcopy(merged)

    def _upsert_into(self, data: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
        skin_id = str(record.get("id") or "")
        if not skin_id:
            raise ValueError("A skin id is required.")
        previous = data["records"].get(skin_id) if isinstance(data["records"].get(skin_id), dict) else {}
        merged = dict(previous)
        merged.update(record)
        merged["id"] = skin_id
        model = str(merged.get("model") or MODEL_STEVE)
        merged["model"] = model if model in MODELS else MODEL_STEVE
        merged["favorite"] = skin_id in data["favorites"]
        data["records"][skin_id] = merged
        return merged

    def set_favorite(self, skin_id: str, favorite: bool) -> dict[str, Any]:
        with self._lock:
            return self._set_favorite_unlocked(skin_id, favorite)

    def _set_favorite_unlocked(self, skin_id: str, favorite: bool) -> dict[str, Any]:
        data = self.load()
        record = data["records"].get(skin_id)
        if not isinstance(record, dict):
            raise ValueError("Search or import that skin before adding it to favorites.")
        favorites = [item for item in data["favorites"] if item != skin_id]
        if favorite:
            favorites.insert(0, skin_id)
        data["favorites"] = favorites
        record["favorite"] = favorite
        data["records"][skin_id] = record
        self.save(data)
        return copy.deepcopy(record)

    def favorites(self) -> list[dict[str, Any]]:
        data = self.load()
        items: list[dict[str, Any]] = []
        for skin_id in data["favorites"]:
            record = data["records"].get(skin_id)
            if isinstance(record, dict):
                item = copy.deepcopy(record)
                item["favorite"] = True
                items.append(item)
        return items

    def remember(self, skin_id: str) -> dict[str, Any]:
        with self._lock:
            return self._remember_unlocked(skin_id)

    def _remember_unlocked(self, skin_id: str) -> dict[str, Any]:
        data = self.load()
        record = data["records"].get(skin_id)
        if not isinstance(record, dict):
            raise ValueError("That skin is not in the local library yet.")
        recent = [item for item in data["recent"] if item.get("id") != skin_id]
        recent.insert(0, {"id": skin_id, "viewedAt": _now()})
        data["recent"] = recent[:RECENT_LIMIT]
        self.save(data)
        item = copy.deepcopy(record)
        item["favorite"] = skin_id in data["favorites"]
        item["viewedAt"] = data["recent"][0]["viewedAt"]
        return item

    def recent(self) -> list[dict[str, Any]]:
        data = self.load()
        items: list[dict[str, Any]] = []
        for entry in data["recent"]:
            skin_id = entry.get("id")
            record = data["records"].get(skin_id)
            if not isinstance(record, dict):
                continue
            item = copy.deepcopy(record)
            item["favorite"] = skin_id in data["favorites"]
            item["viewedAt"] = entry.get("viewedAt")
            items.append(item)
        return items

    def set_model(self, skin_id: str, model: str) -> dict[str, Any]:
        if model not in MODELS:
            raise ValueError("Skin model must be steve or alex.")
        with self._lock:
            return self._set_model_unlocked(skin_id, model)

    def _set_model_unlocked(self, skin_id: str, model: str) -> dict[str, Any]:
        data = self.load()
        record = data["records"].get(skin_id)
        if not isinstance(record, dict):
            raise ValueError("Search or import that skin before choosing a model.")
        record["model"] = model
        data["records"][skin_id] = record
        self.save(data)
        item = copy.deepcopy(record)
        item["favorite"] = skin_id in data["favorites"]
        return item

    def write_import(self, png: bytes, *, name: str, model: str, width: int, height: int) -> dict[str, Any]:
        skin_id = "local-" + uuid.uuid4().hex
        filename = skin_id + ".png"
        path = self.files / filename
        path.write_bytes(png)
        record = {
            "id": skin_id,
            "name": name,
            "username": None,
            "source": "local-png",
            "model": model if model in MODELS else MODEL_STEVE,
            "favorite": False,
            "width": width,
            "height": height,
            "file": f"skins/files/{filename}",
            "previewPath": f"/api/skins/texture?id={skin_id}",
            "uploadedToMicrosoft": False,
        }
        return self.upsert(record)

    def read_bytes(self, record: dict[str, Any]) -> bytes | None:
        relative = record.get("file")
        if not isinstance(relative, str) or not relative:
            return None
        path = self._safe_data_path(relative)
        if path is None or not path.is_file():
            return None
        return path.read_bytes()

    def write_account_skin(self, account_id: str, png: bytes, meta: dict[str, Any]) -> str:
        folder = self.local_accounts / _safe_account_dir(account_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "skin.png").write_bytes(png)
        write_json(folder / "skin.json", meta)
        return f"skins/local-accounts/{folder.name}/skin.png"

    def _safe_data_path(self, relative: str) -> Path | None:
        if relative.startswith("/") or ".." in Path(relative).parts:
            return None
        path = (self.directory / relative).resolve()
        root = self.directory.resolve()
        if path != root and root not in path.parents:
            return None
        return path


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_account_dir(account_id: str) -> str:
    cleaned = "".join(ch for ch in account_id if ch.isalnum() or ch in "-_")
    if not cleaned or cleaned != account_id:
        raise ValueError("That account id cannot be used as a skin folder name.")
    return cleaned
