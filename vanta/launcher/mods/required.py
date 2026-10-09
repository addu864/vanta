"""Required Fabric 1.21.11 mods for instances Vanta installs.

There is no toggle. Official Modrinth files only. A project with no version
that lists Minecraft 1.21.11 and Fabric is skipped. Jars already in the mods
folder are left in place. This does not launch Minecraft and does not copy
another client.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vanta.launcher.mods.compatibility import is_compatible
from vanta.launcher.mods.manager import SAFE_NAME
from vanta.launcher.mods.models import TARGET_GAME_VERSION, TARGET_LOADER, ModFile, ModVersion
from vanta.launcher.mods.modrinth import ModrinthRepository
from vanta.launcher.mods.repository import ModRepository, ModRepositoryError

MANIFEST_NAME = "required-mods.json"
# Starlight ships inside Lithium. A separate jar is never requested.
EXCLUDED_SLUGS = frozenset({"starlight", "starlight-fabric", "starlight-unofficial", "fastclient"})
CHEAT_MARKERS = (
    "xray",
    "killaura",
    "kill-aura",
    "kill_aura",
    "wurst",
    "meteor-client",
    "liquidbounce",
    "inertia",
    "aristois",
    "chestesp",
    "baritone",
)


@dataclass(frozen=True)
class RequiredMod:
    id: str
    slug: str
    title: str
    group: str


# Slugs checked against api.modrinth.com. ferrite-core is the live project;
# ferritecore 404s. ModernFix is listed so a missing 1.21.11 build is reported.
REQUIRED_MODS: tuple[RequiredMod, ...] = (
    RequiredMod("sodium", "sodium", "Sodium", "performance"),
    RequiredMod("lithium", "lithium", "Lithium", "performance"),
    RequiredMod("immediatelyfast", "immediatelyfast", "ImmediatelyFast", "performance"),
    RequiredMod("modernfix", "modernfix", "ModernFix", "performance"),
    RequiredMod("entityculling", "entityculling", "Entity Culling", "performance"),
    RequiredMod("ferrite-core", "ferrite-core", "FerriteCore", "performance"),
    RequiredMod("dynamic-fps", "dynamic-fps", "Dynamic FPS", "performance"),
    RequiredMod("bobby", "bobby", "Bobby", "utility"),
    RequiredMod("fastquit", "fastquit", "FastQuit", "utility"),
    RequiredMod("mouse-tweaks", "mouse-tweaks", "Mouse Tweaks", "utility"),
    RequiredMod("appleskin", "appleskin", "AppleSkin", "utility"),
    RequiredMod("entitytexturefeatures", "entitytexturefeatures", "Entity Texture Features", "utility"),
    RequiredMod("entity-model-features", "entity-model-features", "Entity Model Features", "utility"),
    RequiredMod("craftpresence", "craftpresence", "CraftPresence", "utility"),
    # Open to LAN -> public join address, no port forwarding. e4mc fork that
    # also lets offline/local accounts join (Online Mode toggle on the LAN screen).
    RequiredMod("e4all", "e4all", "e4all", "utility"),
    # In-game Mods screen with config buttons (uses Cloth Config / YACL screens).
    RequiredMod("modmenu", "modmenu", "Mod Menu", "utility"),
    RequiredMod("cloth-config", "cloth-config", "Cloth Config API", "utility"),
    RequiredMod("yacl", "yacl", "YetAnotherConfigLib", "utility"),
    RequiredMod("fullbright", "fullbright", "Fullbright", "display"),
    RequiredMod("overflowing-bars", "overflowing-bars", "Overflowing Bars", "display"),
    RequiredMod("health-indicators", "health-indicators", "Health Indicators", "display"),
)


def install_required_mods(
    mods_dir: Path,
    *,
    repository: ModRepository | None = None,
    game_version: str = TARGET_GAME_VERSION,
    loader: str = TARGET_LOADER,
) -> dict[str, Any]:
    """Download the required set into ``mods_dir``. Never deletes unrelated jars."""
    folder = Path(mods_dir)
    folder.mkdir(parents=True, exist_ok=True)
    repo = repository or ModrinthRepository(timeout=60)
    installed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    seen_projects: set[str] = set()
    previous = _previous_files(folder)

    if game_version != TARGET_GAME_VERSION or loader != TARGET_LOADER:
        return {
            "ok": True,
            "installed": [],
            "kept": [],
            "skipped": [
                {
                    "id": item.id,
                    "slug": item.slug,
                    "title": item.title,
                    "reason": f"No required-mod install for {game_version} {loader}.",
                }
                for item in REQUIRED_MODS
            ],
            "minecraftLaunched": False,
        }

    pending: list[tuple[str, str, str]] = [(item.id, item.slug, item.title) for item in REQUIRED_MODS]
    # Required library dependencies (Fabric API and others) are filled in as found.
    extra: list[tuple[str, str, str]] = []
    for mod_id, slug, title in pending:
        _place_one(
            repo,
            folder,
            mod_id=mod_id,
            slug=slug,
            title=title,
            game_version=game_version,
            loader=loader,
            installed=installed,
            skipped=skipped,
            kept=kept,
            seen_projects=seen_projects,
            extra=extra,
            previous=previous,
        )
    for mod_id, slug, title in extra:
        if slug in {item[1] for item in pending}:
            continue
        _place_one(
            repo,
            folder,
            mod_id=mod_id,
            slug=slug,
            title=title,
            game_version=game_version,
            loader=loader,
            installed=installed,
            skipped=skipped,
            kept=kept,
            seen_projects=seen_projects,
            extra=None,
            previous=previous,
        )

    _write_manifest(folder, installed, kept)
    return {
        "ok": True,
        "installed": installed,
        "kept": kept,
        "skipped": skipped,
        "minecraftLaunched": False,
    }


def _place_one(
    repo: ModRepository,
    folder: Path,
    *,
    mod_id: str,
    slug: str,
    title: str,
    game_version: str,
    loader: str,
    installed: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    kept: list[dict[str, Any]],
    seen_projects: set[str],
    extra: list[tuple[str, str, str]] | None,
    previous: dict[str, str] | None = None,
) -> None:
    if _blocked(slug, title):
        skipped.append({"id": mod_id, "slug": slug, "title": title, "reason": "Excluded."})
        return
    try:
        project = repo.get_project(slug)
    except ModRepositoryError as exc:
        skipped.append({"id": mod_id, "slug": slug, "title": title, "reason": exc.message})
        return
    if project.project_type and project.project_type != "mod":
        skipped.append({"id": mod_id, "slug": slug, "title": project.title, "reason": "Not a mod."})
        return
    if _blocked(project.slug, project.title):
        skipped.append({"id": mod_id, "slug": project.slug, "title": project.title, "reason": "Excluded."})
        return
    if project.id in seen_projects:
        return
    try:
        versions = repo.get_versions(project.id or project.slug, game_version=game_version, loader=loader)
    except ModRepositoryError as exc:
        skipped.append({"id": mod_id, "slug": project.slug, "title": project.title, "reason": exc.message})
        return
    chosen = _compatible(versions, game_version, loader)
    if chosen is None:
        skipped.append(
            {
                "id": mod_id,
                "slug": project.slug,
                "title": project.title,
                "reason": f"No {game_version} {loader} build.",
            }
        )
        return
    version, mod_file = chosen
    seen_projects.add(project.id or project.slug)
    if extra is not None:
        _queue_required_deps(repo, version, extra, seen_projects)
    safe_name = _safe_jar_name(mod_file.filename, project.slug)
    target = folder / safe_name
    record = {
        "id": mod_id,
        "slug": project.slug,
        "title": project.title,
        "projectId": project.id,
        "versionId": version.id,
        "versionNumber": version.version_number,
        "filename": safe_name,
        "sha1": (mod_file.hashes or {}).get("sha1"),
    }
    if target.is_file() and _file_matches(target, mod_file):
        record["kept"] = True
        kept.append(record)
        return
    try:
        payload = repo.download_file(mod_file.url)
    except ModRepositoryError as exc:
        skipped.append({"id": mod_id, "slug": project.slug, "title": project.title, "reason": exc.message})
        seen_projects.discard(project.id or project.slug)
        return
    if not payload or not _payload_matches(payload, mod_file):
        skipped.append(
            {
                "id": mod_id,
                "slug": project.slug,
                "title": project.title,
                "reason": "Downloaded file did not match the Modrinth hash.",
            }
        )
        seen_projects.discard(project.id or project.slug)
        return
    target.write_bytes(payload)
    record["kept"] = False
    # A newer build of a jar Vanta installed earlier replaces it. Two jars of one
    # mod make Fabric refuse to start, so the old one is removed.
    old_name = (previous or {}).get(project.id) or (previous or {}).get(project.slug)
    if old_name and old_name != safe_name:
        old_path = folder / old_name
        if old_path.is_file() and old_path.parent == folder:
            old_path.unlink()
            record["replaced"] = old_name
    installed.append(record)


def _queue_required_deps(
    repo: ModRepository,
    version: ModVersion,
    extra: list[tuple[str, str, str]],
    seen_projects: set[str],
) -> None:
    queued = {item[1] for item in extra}
    for dep in version.dependencies:
        if dep.dependency_type != "required" or not dep.project_id:
            continue
        if dep.project_id in seen_projects:
            continue
        try:
            project = repo.get_project(dep.project_id)
        except ModRepositoryError:
            extra.append((dep.project_id, dep.project_id, dep.project_id))
            continue
        if _blocked(project.slug, project.title):
            continue
        if project.slug in queued or project.id in seen_projects:
            continue
        extra.append((project.slug, project.slug, project.title))
        queued.add(project.slug)


def _compatible(
    versions: list[ModVersion],
    game_version: str,
    loader: str,
) -> tuple[ModVersion, ModFile] | None:
    for version in versions:
        if not is_compatible(version, game_version=game_version, loader=loader):
            continue
        # Desktop launcher: skip Android-only builds (e.g. e4all "2.2.0-fabric-android").
        if "android" in str(version.version_number or "").lower():
            continue
        mod_file = _official_file(version)
        if mod_file is not None:
            return version, mod_file
    return None


def _official_file(version: ModVersion) -> ModFile | None:
    files = []
    for item in version.files:
        name = item.filename.lower()
        if not item.url.startswith("https://") or not name.endswith(".jar"):
            continue
        if name.endswith(("-sources.jar", "-dev.jar", "-javadoc.jar")):
            continue
        host = (urlparse(item.url).hostname or "").lower()
        if host != "cdn.modrinth.com" and not host.endswith(".modrinth.com"):
            continue
        files.append(item)
    if not files:
        return None
    return next((item for item in files if item.primary), files[0])


def _blocked(slug: str, title: str) -> bool:
    folded = f"{slug} {title}".lower().replace(" ", "")
    spaced = f"{slug} {title}".lower()
    if slug.lower() in EXCLUDED_SLUGS:
        return True
    return any(marker in folded or marker in spaced for marker in CHEAT_MARKERS)


def _safe_jar_name(filename: str, slug: str) -> str:
    safe = SAFE_NAME.sub("_", filename).strip("._") or f"{slug}.jar"
    if not safe.lower().endswith(".jar"):
        safe += ".jar"
    return safe


def _file_matches(path: Path, mod_file: ModFile) -> bool:
    try:
        payload = path.read_bytes()
    except OSError:
        return False
    if mod_file.size and len(payload) != int(mod_file.size):
        return False
    return _payload_matches(payload, mod_file)


def _payload_matches(payload: bytes, mod_file: ModFile) -> bool:
    hashes = {str(key).lower(): str(value).lower() for key, value in (mod_file.hashes or {}).items()}
    if hashes.get("sha1"):
        return hashlib.sha1(payload).hexdigest() == hashes["sha1"]
    if hashes.get("sha512"):
        return hashlib.sha512(payload).hexdigest() == hashes["sha512"]
    return bool(payload)


def _previous_files(folder: Path) -> dict[str, str]:
    """projectId/slug -> jar filename from the last required-mods.json."""
    path = folder / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    mapping: dict[str, str] = {}
    if not isinstance(data, dict):
        return mapping
    for item in [*(data.get("kept") or []), *(data.get("installed") or [])]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("filename") or "")
        if not name or name != Path(name).name or not name.endswith(".jar"):
            continue
        for key in (item.get("projectId"), item.get("slug")):
            if isinstance(key, str) and key:
                mapping[key] = name
    return mapping


def _write_manifest(folder: Path, installed: list[dict[str, Any]], kept: list[dict[str, Any]]) -> None:
    names = []
    for item in [*kept, *installed]:
        filename = str(item.get("filename") or "")
        if filename and filename not in names:
            names.append(filename)
    payload = {"filenames": names, "installed": installed, "kept": kept}
    (folder / MANIFEST_NAME).write_text(json.dumps(payload, indent=2), encoding="utf-8")
