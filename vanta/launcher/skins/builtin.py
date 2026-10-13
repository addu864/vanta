"""Vanta's own skin library. Files live in assets/skins. No Ely.by or NameMC calls."""

from __future__ import annotations

import json
import re
import urllib.request
import zipfile
from pathlib import Path

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
SKINDEX_SITE = "https://www.skindex.pro/"
SKINDEX_TEXTURE = "https://www.skindex.pro/static/skins/{name}"
SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+\.png$")

# Minecraft's own default skins. Not shipped with Vanta: read on demand from the
# player's own client.jar (downloaded from Mojang on first run).
DEFAULT_SKINS = ("steve", "alex", "ari", "efe", "kai", "makena", "noor", "sunny", "zuri")
DEFAULT_NOTE = "Minecraft default skin, read from your own Minecraft files (Mojang)."
DEFAULT_CATEGORY = "Minecraft defaults"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Vanta launcher (skin preview)"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read(256 * 1024)


class VantaSkinLibrary(SkinRepository):
    name = "vanta-library"

    def __init__(self, directory=None, data_dir=None, fetch=None) -> None:
        self.directory = directory or (repo_root() / "assets" / "skins")
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self._fetch = fetch or _fetch

    def _cache_dir(self) -> Path:
        base = self.data_dir if self.data_dir is not None else self.directory.parent.parent / ".vanta-data"
        return base / "skin-cache"

    def _client_jar(self) -> Path | None:
        if self.data_dir is None:
            return None
        from vanta.launcher.minecraft.first_run import game_root
        jar = game_root(self.data_dir) / "1.21.11-fabric" / "client.jar"
        return jar if jar.is_file() else None

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": "Vanta skin library",
            "secretRequired": False,
            "catalogSearch": True,
            "offline": True,
            "lookup": "local-library",
            "limitation": (
                "Vanta's own skins are stored in the app. Skindex skins are downloaded from "
                "skindex.pro when you open them and cached on this computer; credit: Skindex (skindex.pro). "
                "Minecraft's default skins are read from your own Minecraft files. "
                "Ely.by and NameMC are not contacted. "
                "You can still import a PNG. "
                "Saving a skin stays on the LOCAL TEST ACCOUNT and is not a Microsoft upload."
            ),
        }

    def _items(self) -> list[dict]:
        items = [dict(item) for item in CATALOG]
        for name in DEFAULT_SKINS:
            for arms, model in (("wide", MODEL_STEVE), ("slim", MODEL_ALEX)):
                items.append({
                    "id": f"mojang-{name}-{arms}",
                    "name": f"{name.capitalize()} ({'slim' if arms == 'slim' else 'classic'})",
                    "model": model,
                    "file": f"mojang:{arms}/{name}.png",
                    "category": DEFAULT_CATEGORY,
                    "sourceNote": DEFAULT_NOTE,
                })
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
        data = self._load(wanted["file"])
        try:
            inspect_skin_png(data)
        except ValueError as exc:
            raise SkinRepositoryError(str(exc)) from exc
        return data

    def _load(self, file: str) -> bytes:
        if file.startswith("mojang:"):
            jar = self._client_jar()
            if jar is None:
                raise SkinRepositoryError("Minecraft's default skins appear after Minecraft 1.21.11 is downloaded.")
            member = "assets/minecraft/textures/entity/player/" + file.split(":", 1)[1]
            try:
                with zipfile.ZipFile(jar) as zf:
                    return zf.read(member)
            except (OSError, KeyError, zipfile.BadZipFile) as exc:
                raise SkinRepositoryError("That default skin isn't in this Minecraft version.") from exc
        if file.startswith("skindex/"):
            name = file.rsplit("/", 1)[-1]
            if not SAFE_FILE.match(name):
                raise SkinRepositoryError("Bad skin name.")
            cached = self._cache_dir() / "skindex" / name
            if cached.is_file():
                return cached.read_bytes()
            try:
                data = self._fetch(SKINDEX_TEXTURE.format(name=name))
                inspect_skin_png(data)
            except (OSError, ValueError) as exc:
                raise SkinRepositoryError("Couldn't download that skin from skindex.pro. Check your internet connection.") from exc
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_name(cached.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(cached)
            return data
        path = self.directory / file
        if not path.is_file():
            raise SkinRepositoryError("That Vanta library skin file is missing.")
        return path.read_bytes()
