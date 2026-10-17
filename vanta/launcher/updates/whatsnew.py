"""'What's new' notes for the Home dashboard, from GitHub release notes (cached on disk)."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from vanta import __version__
from vanta.launcher.updates import github

CACHE_SECONDS = 6 * 3600
RETRY_SECONDS = 600
_last_fail = {"at": 0.0}
_BULLET = re.compile(r"^\s*[-*•]\s+(.*\S)\s*$")
_MD = re.compile(r"(\*\*|__|`)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def _short(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    cut = text.find(". ", 40)
    if 0 < cut < limit:
        return text[:cut + 1]
    return text[:limit - 1].rstrip() + "…"


def highlights(body: str, limit: int = 5) -> list[str]:
    """Plain-text bullet points from a markdown release body."""
    out: list[str] = []
    for line in str(body or "").splitlines():
        m = _BULLET.match(line)
        if not m:
            continue
        text = _MD.sub("", _LINK.sub(r"\1", m.group(1))).strip()
        if text and len(text) > 3:
            out.append(_short(text))
        if len(out) >= limit:
            break
    return out


def summarize(releases: list[dict[str, Any]], current: str = __version__, count: int = 3) -> list[dict[str, Any]]:
    rows = []
    for rel in releases or []:
        tag = str(rel.get("tag_name") or "")
        if rel.get("draft") or rel.get("prerelease") or not re.fullmatch(r"v\d+(\.\d+)*", tag):
            continue
        rows.append({
            "tag": tag, "name": str(rel.get("name") or tag)[:120], "date": str(rel.get("published_at") or "")[:10],
            "url": str(rel.get("html_url") or ""), "highlights": highlights(str(rel.get("body") or "")),
            "current": github.version_tuple(tag) == github.version_tuple(current),
            "newer": github.version_tuple(tag) > github.version_tuple(current),
        })
    rows.sort(key=lambda r: github.version_tuple(r["tag"]), reverse=True)
    return rows[:count]


def _has_version(releases: list[Any], current: str) -> bool:
    tags = {str(r.get("tag_name") or "") for r in releases if isinstance(r, dict)}
    return f"v{current}" in tags or f"v{current}-mac" in tags


# Shipped with the app, so the popup after an update works offline too.
BUNDLED_HIGHLIGHTS: dict[str, list[str]] = {
    "1.6.2": [
        "Downloads panel: see everything Vanta is downloading. Pause, resume or cancel Minecraft downloads.",
        "Clearer empty states, error messages and loading placeholders.",
        "What's new now works offline right after an update.",
    ],
    "1.6.1": [
        "What's new popup after an update.",
        "Logs viewer: filter as you type, errors and warnings only, copy matching lines.",
        "Screenshot viewer: full size, arrow keys, copy image, show in folder.",
        "Server favourites, sort by players, ping or name.",
        "Preview images on shader and resource pack cards.",
    ],
}


def _quick_json(url: str) -> Any:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": f"Vanta/{__version__}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=6) as resp:
        return json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))


def whats_new(cache_file: Path, fetch: Callable[[str], Any] = _quick_json, current: str = __version__,
              now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    cached: dict[str, Any] = {}
    try:
        cached = json.loads(Path(cache_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    fresh = isinstance(cached, dict) and isinstance(cached.get("releases"), list) and now - float(cached.get("at") or 0) < CACHE_SECONDS
    if fresh and not _has_version(cached["releases"], current):
        fresh = False  # saved before this version was released (e.g. right after an update): fetch again
    offline = False
    if not fresh and fetch is _quick_json and now - _last_fail["at"] < RETRY_SECONDS:
        offline = True
    elif not fresh:
        try:
            data = fetch(f"{github.API}/releases?per_page=15")
            if not isinstance(data, list):
                raise ValueError("unexpected reply")
            slim = [{k: r.get(k) for k in ("tag_name", "name", "published_at", "html_url", "body", "draft", "prerelease")}
                    for r in data if isinstance(r, dict)]
            cached = {"at": now, "releases": slim}
            try:
                Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
                Path(cache_file).write_text(json.dumps(cached), encoding="utf-8")
            except OSError:
                pass
        except Exception:  # noqa: BLE001 - offline is normal
            offline = True
            _last_fail["at"] = now
    releases = summarize((cached or {}).get("releases") or [], current)
    return {"ok": True, "current": current, "releases": releases, "offline": offline and not releases,
            "stale": offline and bool(releases)}
