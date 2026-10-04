"""Ely.by public skinsystem client. No API secret.

Documented endpoints (https://docs.ely.by/en/skins-system.html):

- GET https://skinsystem.ely.by/textures/{nickname}?version=2
- GET https://skinsystem.ely.by/skins/{nickname}.png

This is a username lookup, not a catalog crawl. NameMC is not called.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from vanta.launcher.skins.models import MODEL_ALEX, MODEL_STEVE, SkinHit
from vanta.launcher.skins.pngskin import inspect_skin_png
from vanta.launcher.skins.repository import SkinRepository, SkinRepositoryError

DEFAULT_BASE = "https://skinsystem.ely.by"
USER_AGENT = "Vanta/0.3 (local launcher; Advay/ADDU; no-secret; contact=local)"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
MAX_LOOKUP = 5


class ElyByRepository(SkinRepository):
    name = "elyby"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE,
        opener: Callable[..., Any] | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "baseUrl": self.base_url,
            "secretRequired": False,
            "catalogSearch": False,
            "lookup": "minecraft-username",
            "limitation": (
                "Ely.by public skinsystem looks up a Minecraft username. "
                "It is not a skin-catalog keyword search. "
                "NameMC has no permitted public skin-search API, so Vanta does not scrape it."
            ),
        }

    def search(self, query: str, *, limit: int = 5) -> list[SkinHit]:
        names = _usernames(query, limit=max(1, min(int(limit or 1), MAX_LOOKUP)))
        if not names:
            raise SkinRepositoryError(
                "Enter a Minecraft username (letters, numbers, underscore, up to 16 characters). "
                "Ely.by's public API does not search the skin catalog by title, and "
                "NameMC has no permitted public skin-search API."
            )
        hits: list[SkinHit] = []
        for name in names:
            hit = self._lookup(name)
            if hit is not None:
                hits.append(hit)
        return hits

    def fetch_texture(self, skin_id: str) -> bytes:
        username = _username_from_id(skin_id)
        if username is None:
            raise SkinRepositoryError("That skin id is not an Ely.by username lookup.")
        url = f"{self.base_url}/skins/{urllib.parse.quote(username)}.png"
        data = self._read(url, accept="image/png")
        if not data:
            raise SkinRepositoryError(f"Ely.by has no skin PNG for '{username}'.")
        try:
            inspect_skin_png(data)
        except ValueError as exc:
            raise SkinRepositoryError(str(exc)) from exc
        return data

    def _lookup(self, username: str) -> SkinHit | None:
        url = f"{self.base_url}/textures/{urllib.parse.quote(username)}?version=2"
        try:
            raw, status = self._read_status(url, accept="application/json")
        except SkinRepositoryError:
            raise
        if status == 204 or not raw:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkinRepositoryError("Ely.by textures response was not JSON.") from exc
        if not isinstance(payload, dict):
            raise SkinRepositoryError("Ely.by textures response was not an object.")
        skin = payload.get("SKIN")
        if not isinstance(skin, dict) or not skin.get("url"):
            return None
        metadata = skin.get("metadata") if isinstance(skin.get("metadata"), dict) else {}
        model = MODEL_ALEX if str(metadata.get("model") or "").lower() == "slim" else MODEL_STEVE
        return SkinHit(
            id=f"ely:{username.lower()}",
            name=username,
            username=username,
            source=self.name,
            model=model,
            remote_texture=f"{self.base_url}/skins/{urllib.parse.quote(username)}.png",
        )

    def _read(self, url: str, *, accept: str) -> bytes:
        raw, _status = self._read_status(url, accept=accept)
        return raw

    def _read_status(self, url: str, *, accept: str) -> tuple[bytes, int]:
        if not url.startswith("https://"):
            raise SkinRepositoryError("Skin requests must use https.")
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": accept},
            method="GET",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return b"", 404
            if exc.code >= 500 or exc.code in (408, 429):
                raise SkinRepositoryError(
                    f"Network error while contacting Ely.by (HTTP {exc.code})."
                ) from exc
            raise SkinRepositoryError(f"Ely.by request failed (HTTP {exc.code}).") from exc
        except urllib.error.URLError as exc:
            raise SkinRepositoryError(
                f"Network error while contacting Ely.by: {exc.reason}."
            ) from exc
        except TimeoutError as exc:
            raise SkinRepositoryError("Network error while contacting Ely.by: timed out.") from exc
        if not isinstance(body, (bytes, bytearray)):
            raise SkinRepositoryError("Ely.by returned an unexpected response.")
        return bytes(body), status


def _usernames(query: str, *, limit: int) -> list[str]:
    text = (query or "").strip()
    if not text:
        return []
    parts = re.split(r"[\s,]+", text)
    names: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        if not USERNAME_RE.fullmatch(part):
            # A title-like query is not a silent empty search.
            if len(parts) == 1:
                return []
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(part)
        if len(names) >= limit:
            break
    return names


def _username_from_id(skin_id: str) -> str | None:
    text = (skin_id or "").strip()
    if not text.lower().startswith("ely:"):
        return None
    username = text.split(":", 1)[1]
    if not USERNAME_RE.fullmatch(username):
        return None
    return username
