"""Download the official server jar for a Vanta server.

Paper comes from the PaperMC Fill v3 API (latest STABLE build for the chosen
Minecraft version, sha256 checked). Fabric comes from the Fabric meta API
(latest stable loader + installer). Nothing else is downloaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Callable

USER_AGENT = "Vanta-Launcher/1.0 (+https://github.com/addu864/vanta)"
PAPER_API = "https://fill.papermc.io/v3/projects/paper/versions/{version}/builds"
FABRIC_LOADERS = "https://meta.fabricmc.net/v2/versions/loader/{version}"
FABRIC_INSTALLERS = "https://meta.fabricmc.net/v2/versions/installer"
FABRIC_JAR = "https://meta.fabricmc.net/v2/versions/loader/{version}/{loader}/{installer}/server/jar"
ALLOWED_DOWNLOAD_HOSTS = ("fill-data.papermc.io", "fill.papermc.io", "api.papermc.io", "meta.fabricmc.net")

Opener = Callable[..., Any]


class DownloadError(Exception):
    pass


def resolve(software: str, version: str, opener: Opener | None = None) -> dict[str, Any]:
    """Return {"url", "name", "sha256" (or None), "build"} for the server jar."""
    open_url = opener or urllib.request.urlopen
    if software == "paper":
        return _resolve_paper(version, open_url)
    if software == "fabric":
        return _resolve_fabric(version, open_url)
    raise DownloadError("Software must be Paper or Fabric.")


def download(info: dict[str, Any], destination: Path, opener: Opener | None = None) -> dict[str, Any]:
    """Stream the jar to destination atomically, checking sha256 when known."""
    open_url = opener or urllib.request.urlopen
    url = str(info.get("url") or "")
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or host not in ALLOWED_DOWNLOAD_HOSTS:
        raise DownloadError("Refusing to download a server jar from an unexpected host.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with open_url(request, timeout=120) as response, temporary.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        temporary.unlink(missing_ok=True)
        raise DownloadError(f"Could not download the server jar: {exc}") from exc
    expected = info.get("sha256")
    if expected and digest.hexdigest().lower() != str(expected).lower():
        temporary.unlink(missing_ok=True)
        raise DownloadError("The downloaded server jar failed its sha256 check. It was deleted.")
    if size < 1024:
        temporary.unlink(missing_ok=True)
        raise DownloadError("The downloaded server jar was empty.")
    os.replace(temporary, destination)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _get_json(url: str, open_url: Opener) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with open_url(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DownloadError("That Minecraft version is not available for this server software.") from exc
        raise DownloadError(f"The download service answered HTTP {exc.code}.") from exc
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise DownloadError(f"Could not reach the download service: {exc}") from exc


def _resolve_paper(version: str, open_url: Opener) -> dict[str, Any]:
    builds = _get_json(PAPER_API.format(version=version), open_url)
    if isinstance(builds, dict):
        builds = builds.get("builds") or [builds]
    if not isinstance(builds, list) or not builds:
        raise DownloadError(f"PaperMC has no builds for Minecraft {version} yet.")
    candidates = [b for b in builds if isinstance(b, dict)]
    stable = [b for b in candidates if str(b.get("channel") or "").upper() == "STABLE"]
    pool = stable or candidates
    best = max(pool, key=lambda b: int(b.get("id") or 0))
    server = ((best.get("downloads") or {}).get("server:default")) or {}
    url = server.get("url")
    if not url:
        raise DownloadError(f"PaperMC build {best.get('id')} has no server download.")
    return {
        "url": url,
        "name": server.get("name") or f"paper-{version}-{best.get('id')}.jar",
        "sha256": (server.get("checksums") or {}).get("sha256"),
        "build": best.get("id"),
        "channel": best.get("channel"),
    }


def _resolve_fabric(version: str, open_url: Opener) -> dict[str, Any]:
    loaders = _get_json(FABRIC_LOADERS.format(version=version), open_url)
    if not isinstance(loaders, list) or not loaders:
        raise DownloadError(f"Fabric has no loader for Minecraft {version}.")
    stable = [row for row in loaders if isinstance(row, dict) and (row.get("loader") or {}).get("stable")]
    loader = ((stable or loaders)[0].get("loader") or {}).get("version")
    installers = _get_json(FABRIC_INSTALLERS, open_url)
    if not isinstance(installers, list) or not installers:
        raise DownloadError("Fabric installer list is empty.")
    stable_inst = [row for row in installers if isinstance(row, dict) and row.get("stable")]
    installer = (stable_inst or installers)[0].get("version")
    if not loader or not installer:
        raise DownloadError("Could not pick a Fabric loader and installer.")
    return {
        "url": FABRIC_JAR.format(version=version, loader=loader, installer=installer),
        "name": f"fabric-server-{version}-{loader}.jar",
        "sha256": None,
        "build": loader,
        "channel": "stable",
    }
