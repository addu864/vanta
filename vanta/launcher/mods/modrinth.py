"""Modrinth public API client (https://api.modrinth.com/v2 only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from vanta.launcher.mods.models import ModDependency, ModFile, ModProject, ModVersion
from vanta.launcher.mods.repository import ModRepository, ModRepositoryError

DEFAULT_BASE = "https://api.modrinth.com/v2"
USER_AGENT = "Vanta/0.2 (local launcher; Advay/ADDU; no-premium; contact=local)"


class ModrinthRepository(ModRepository):
    name = "modrinth"

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

    def search(
        self,
        query: str,
        *,
        game_version: str,
        loader: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ModProject]:
        facets = json.dumps(
            [
                ["project_type:mod"],
                [f"versions:{game_version}"],
                [f"categories:{loader}"],
            ]
        )
        params = {
            "query": query or "",
            "limit": str(max(1, min(limit, 100))),
            "offset": str(max(0, offset)),
            "facets": facets,
        }
        data = self._get_json("/search", params)
        hits = data.get("hits") if isinstance(data, dict) else None
        if not isinstance(hits, list):
            raise ModRepositoryError("Modrinth search returned an unexpected response.")
        return [self._project_from_search(item) for item in hits if isinstance(item, dict)]

    def get_project(self, project_id_or_slug: str) -> ModProject:
        key = (project_id_or_slug or "").strip()
        if not key:
            raise ModRepositoryError("A project id or slug is required.")
        data = self._get_json(f"/project/{urllib.parse.quote(key)}")
        if not isinstance(data, dict):
            raise ModRepositoryError("Modrinth project response was not an object.")
        return self._project_from_detail(data)

    def get_versions(
        self,
        project_id_or_slug: str,
        *,
        game_version: str | None = None,
        loader: str | None = None,
    ) -> list[ModVersion]:
        key = (project_id_or_slug or "").strip()
        if not key:
            raise ModRepositoryError("A project id or slug is required.")
        params: dict[str, str] = {}
        if game_version:
            params["game_versions"] = json.dumps([game_version])
        if loader:
            params["loaders"] = json.dumps([loader])
        data = self._get_json(f"/project/{urllib.parse.quote(key)}/version", params)
        if not isinstance(data, list):
            raise ModRepositoryError("Modrinth versions response was not a list.")
        return [self._version_from_api(item) for item in data if isinstance(item, dict)]

    def get_version(self, version_id: str) -> ModVersion:
        key = (version_id or "").strip()
        if not key:
            raise ModRepositoryError("A version id is required.")
        data = self._get_json(f"/version/{urllib.parse.quote(key)}")
        if not isinstance(data, dict):
            raise ModRepositoryError("Modrinth version response was not an object.")
        return self._version_from_api(data)

    def download_file(self, url: str) -> bytes:
        target = (url or "").strip()
        if not target.startswith("https://"):
            raise ModRepositoryError("Mod downloads must use an https URL from the API.")
        request = urllib.request.Request(
            target,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            method="GET",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise ModRepositoryError(
                f"Could not download the mod file (HTTP {exc.code})."
            ) from exc
        except urllib.error.URLError as exc:
            raise ModRepositoryError(
                f"Network error while downloading the mod file: {exc.reason}."
            ) from exc
        except TimeoutError as exc:
            raise ModRepositoryError("Timed out while downloading the mod file.") from exc

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self.base_url}{path}{query}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                detail = ""
            message = f"Modrinth API request failed (HTTP {exc.code})"
            if detail:
                message += f": {detail}"
            raise ModRepositoryError(message + ".") from exc
        except urllib.error.URLError as exc:
            raise ModRepositoryError(
                f"Network error talking to Modrinth: {exc.reason}."
            ) from exc
        except TimeoutError as exc:
            raise ModRepositoryError("Timed out talking to Modrinth.") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModRepositoryError("Modrinth returned invalid JSON.") from exc

    def _project_from_search(self, item: dict[str, Any]) -> ModProject:
        author = ""
        if isinstance(item.get("author"), str):
            author = item["author"]
        icon = item.get("icon_url")
        icon_url = icon if isinstance(icon, str) and icon.startswith("https://") else None
        return ModProject(
            id=str(item.get("project_id") or item.get("id") or ""),
            slug=str(item.get("slug") or ""),
            title=str(item.get("title") or item.get("slug") or "Untitled"),
            description=str(item.get("description") or ""),
            author=author,
            icon_url=icon_url,
            project_type=str(item.get("project_type") or "mod"),
            game_versions=[str(v) for v in (item.get("versions") or []) if v],
            loaders=[str(v).lower() for v in (item.get("categories") or []) if v],
            source=self.name,
            download_count=int(item.get("downloads") or 0),
            categories=[str(v) for v in (item.get("categories") or []) if v],
        )

    def _project_from_detail(self, item: dict[str, Any]) -> ModProject:
        author = ""
        team = item.get("team")
        if isinstance(item.get("author"), str):
            author = item["author"]
        elif isinstance(team, str):
            author = team
        icon = item.get("icon_url")
        icon_url = icon if isinstance(icon, str) and icon.startswith("https://") else None
        return ModProject(
            id=str(item.get("id") or ""),
            slug=str(item.get("slug") or ""),
            title=str(item.get("title") or item.get("slug") or "Untitled"),
            description=str(item.get("description") or item.get("body") or "")[:500],
            author=author,
            icon_url=icon_url,
            project_type=str(item.get("project_type") or "mod"),
            game_versions=[str(v) for v in (item.get("game_versions") or []) if v],
            loaders=[str(v).lower() for v in (item.get("loaders") or []) if v],
            source=self.name,
            download_count=int(item.get("downloads") or 0),
            categories=[str(v) for v in (item.get("categories") or []) if v],
        )

    def _version_from_api(self, item: dict[str, Any]) -> ModVersion:
        files: list[ModFile] = []
        for raw in item.get("files") or []:
            if not isinstance(raw, dict):
                continue
            url = raw.get("url")
            if not isinstance(url, str) or not url.startswith("https://"):
                continue
            hashes = raw.get("hashes") if isinstance(raw.get("hashes"), dict) else {}
            files.append(
                ModFile(
                    filename=str(raw.get("filename") or "mod.jar"),
                    url=url,
                    size=int(raw.get("size") or 0),
                    primary=bool(raw.get("primary", True)),
                    hashes={str(k): str(v) for k, v in hashes.items()},
                )
            )
        dependencies: list[ModDependency] = []
        for raw in item.get("dependencies") or []:
            if not isinstance(raw, dict):
                continue
            dependencies.append(
                ModDependency(
                    project_id=str(raw["project_id"]) if raw.get("project_id") else None,
                    version_id=str(raw["version_id"]) if raw.get("version_id") else None,
                    dependency_type=str(raw.get("dependency_type") or "required"),
                    file_name=str(raw["file_name"]) if raw.get("file_name") else None,
                )
            )
        return ModVersion(
            id=str(item.get("id") or ""),
            project_id=str(item.get("project_id") or ""),
            name=str(item.get("name") or item.get("version_number") or ""),
            version_number=str(item.get("version_number") or ""),
            game_versions=[str(v) for v in (item.get("game_versions") or []) if v],
            loaders=[str(v).lower() for v in (item.get("loaders") or []) if v],
            files=files,
            dependencies=dependencies,
            version_type=str(item.get("version_type") or "release"),
            source=self.name,
        )
