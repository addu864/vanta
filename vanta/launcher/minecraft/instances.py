"""Downloadable instances: Minecraft 1.18 through 26.3, Fabric or vanilla.

Versions come from Mojang's version manifest (releases only, and only ones
that exist). Fabric availability comes from Fabric meta. Each install goes to
its own folder ``<game>/<version>-fabric`` or ``<version>-vanilla``. Assets are
shared in ``<game>/assets``. The existing 1.21.11-fabric instance is never
touched by this module.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from vanta.launcher.minecraft import install as mc_install

MIN_VERSION = (1, 18)
MAX_VERSION = (26, 3)
PROTECTED = {"1.21.11-fabric"}
META_NAME = "vanta-instance.json"
# Default Fabric mods for downloaded instances (skipped where no build exists).
DEFAULT_MODS: tuple[tuple[str, str], ...] = (
    ("fabric-api", "Fabric API"),
    ("sodium", "Sodium"),
    ("lithium", "Lithium"),
    ("modmenu", "Mod Menu"),
)
CACHE_SECONDS = 600

_cache: dict[str, Any] = {}
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def version_key(version_id: str) -> tuple[int, ...] | None:
    parts = version_id.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def in_range(version_id: str) -> bool:
    key = version_key(version_id)
    if key is None:
        return False
    return MIN_VERSION <= key[:2] <= MAX_VERSION and (key[:2] != MAX_VERSION or len(key) <= 3)


def filter_releases(manifest: dict[str, Any], fabric_games: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    fabric = {str(item.get("version")) for item in (fabric_games or []) if isinstance(item, dict)}
    rows = []
    for item in manifest.get("versions") or []:
        vid = str(item.get("id") or "")
        if item.get("type") != "release" or not in_range(vid):
            continue
        rows.append({
            "id": vid,
            "releaseTime": str(item.get("releaseTime") or "")[:10],
            "fabric": vid in fabric,
            "vanilla": True,
        })
    rows.sort(key=lambda r: version_key(r["id"]) or (), reverse=True)
    return rows


def available_versions(fetch: Callable[[str], Any] = mc_install._fetch_json) -> dict[str, Any]:
    now = time.time()
    if _cache.get("rows") and now - _cache.get("at", 0) < CACHE_SECONDS:
        return {"ok": True, "versions": _cache["rows"], "cached": True}
    try:
        manifest = fetch(mc_install.MANIFEST_URL)
        try:
            games = _fetch_list(f"{mc_install.FABRIC_META.rsplit('/', 1)[0]}/game")
        except Exception:  # noqa: BLE001 - Fabric meta down: offer vanilla only
            games = []
        rows = filter_releases(manifest, games)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Could not load Mojang's version list: {exc}", "versions": []}
    _cache.update(rows=rows, at=now)
    return {"ok": True, "versions": rows, "cached": False}


def _fetch_list(url: str) -> list[Any]:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": mc_install.USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def latest_fabric_loader() -> str:
    loaders = _fetch_list(mc_install.FABRIC_META)
    stable = [item for item in loaders if isinstance(item, dict) and item.get("stable")]
    chosen = (stable or loaders)[0]
    return str(chosen["version"])


def instance_name(version: str, loader: str) -> str:
    return f"{version}-{'fabric' if loader == 'fabric' else 'vanilla'}"


def installed_instances(game_root: Path) -> list[dict[str, Any]]:
    """Instances this module installed (they carry vanta-instance.json and client.jar)."""
    rows = []
    root = Path(game_root)
    if not root.is_dir():
        return rows
    for folder in sorted(root.iterdir()):
        meta = folder / META_NAME
        if not meta.is_file() or not (folder / "client.jar").is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("complete"):
            rows.append(data | {"gameDirectory": str(folder)})
    return rows


def job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        return dict(_jobs.get(job_id) or {"state": "unknown", "id": job_id})


def all_jobs() -> list[dict[str, Any]]:
    with _jobs_lock:
        return [dict(item) for item in _jobs.values()]


def start_install(
    game_root: Path,
    version: str,
    loader: str,
    *,
    installer: Callable[..., dict[str, Any]] = mc_install.install_client,
    mods_installer: Callable[..., dict[str, Any]] | None = None,
    loader_version: Callable[[], str] = latest_fabric_loader,
    background: bool = True,
) -> dict[str, Any]:
    loader = "fabric" if loader == "fabric" else "vanilla"
    if not in_range(version):
        return {"ok": False, "error": f"Version {version} is outside 1.18 to 26.3."}
    name = instance_name(version, loader)
    if name in PROTECTED:
        return {"ok": False, "error": f"{name} is Vanta's main instance and is already installed; it is not re-downloaded here."}
    with _jobs_lock:
        current = _jobs.get(name)
        if current and current.get("state") in {"queued", "downloading"}:
            return {"ok": True, "job": dict(current), "already": True}
        _jobs[name] = {"id": name, "version": version, "loader": loader, "state": "queued", "step": "queued", "done": 0, "total": 1, "percent": 0, "error": ""}

    def update(**fields: Any) -> None:
        with _jobs_lock:
            _jobs[name].update(fields)

    weights = {"metadata": (0, 2), "client": (2, 8), "libraries": (8, 30), "fabric": (30, 35), "assets": (35, 90), "mods": (90, 99)}

    def progress(step: str, done: int, total: int) -> None:
        lo, hi = weights.get(step, (0, 100))
        frac = (done / total) if total else 1.0
        update(state="downloading", step=step, done=done, total=total, percent=int(lo + (hi - lo) * min(1.0, frac)))

    def work() -> None:
        root = Path(game_root)
        try:
            update(state="downloading", step="metadata")
            fabric_loader = loader_version() if loader == "fabric" else None
            result = installer(
                root,
                version=version,
                loader=fabric_loader,
                download_assets=True,
                instance_name=name,
                assets_root=root / "assets",
                progress=progress,
                default_mods=False,
            )
            mods: dict[str, Any] = {"installed": [], "skipped": []}
            if loader == "fabric":
                progress("mods", 0, 1)
                runner = mods_installer or install_default_mods
                mods = runner(root / name / "mods", version)
            meta = {
                "id": name,
                "versionNumber": version,
                "loader": loader,
                "fabricLoader": fabric_loader,
                "javaMajor": result.get("javaMajor"),
                "assetsRoot": str(result.get("assetsRoot") or root / "assets"),
                "defaultMods": [m.get("title") for m in mods.get("installed", []) + mods.get("kept", [])],
                "skippedMods": [f"{m.get('title')}: {m.get('reason')}" for m in mods.get("skipped", [])],
                "complete": True,
                "installedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            (root / name / META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
            update(state="done", step="done", percent=100, result=meta)
        except Exception as exc:  # noqa: BLE001 - report to the UI
            update(state="failed", error=str(exc), trace=traceback.format_exc()[-2000:])

    if background:
        threading.Thread(target=work, name=f"vanta-install-{name}", daemon=True).start()
    else:
        work()
    return {"ok": True, "job": job(name)}


def install_default_mods(mods_dir: Path, game_version: str, repository: Any = None) -> dict[str, Any]:
    """Fabric API, Sodium, Lithium, Mod Menu for this version, where Modrinth has a build."""
    from vanta.launcher.mods import required
    from vanta.launcher.mods.modrinth import ModrinthRepository

    folder = Path(mods_dir)
    folder.mkdir(parents=True, exist_ok=True)
    repo = repository or ModrinthRepository(timeout=60)
    installed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    extra: list[tuple[str, str, str]] = []
    for slug, title in DEFAULT_MODS:
        required._place_one(
            repo, folder, mod_id=slug, slug=slug, title=title, game_version=game_version, loader="fabric",
            installed=installed, skipped=skipped, kept=kept, seen_projects=seen, extra=extra, previous={},
        )
    for mod_id, slug, title in extra:
        required._place_one(
            repo, folder, mod_id=mod_id, slug=slug, title=title, game_version=game_version, loader="fabric",
            installed=installed, skipped=skipped, kept=kept, seen_projects=seen, extra=None, previous={},
        )
    required._write_manifest(folder, installed, kept)
    return {"ok": True, "installed": installed, "kept": kept, "skipped": skipped}
