"""Trailer-look visual set for Fabric 1.21.11 instances Vanta installs.

Resource packs (Bare Bones, Fresh Animations, Bare Bones x Fresh Animations)
go to resourcepacks/ and are enabled in options.txt with the compat patch on
top. Complementary Reimagined (default) and Unbound go to shaderpacks/ and
Iris is pointed at Reimagined. Official Modrinth files only, hash-checked.
The matching mods (Iris, EMF, ETF, Not Enough Animations, Visuality) are in
the required mod set.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from vanta.launcher.mods.models import TARGET_GAME_VERSION, ModFile, ModVersion
from vanta.launcher.mods.modrinth import ModrinthRepository
from vanta.launcher.mods.repository import ModRepository, ModRepositoryError

MANIFEST_NAME = "vanta-visual-set.json"
SAFE = re.compile(r"[^A-Za-z0-9._+ -]")


@dataclass(frozen=True)
class VisualPack:
    key: str
    project: str
    title: str
    kind: str  # "resourcepack" or "shader"


# Bottom to top. options.txt lists the highest-priority pack last.
RESOURCE_PACKS: tuple[VisualPack, ...] = (
    VisualPack("bare-bones", "rox3U8B6", "Bare Bones", "resourcepack"),
    VisualPack("fresh-animations", "50dA9Sha", "Fresh Animations", "resourcepack"),
    VisualPack("bare-bones-x-fresh-animations", "DCHWs5EF", "Bare Bones x Fresh Animations", "resourcepack"),
)
SHADER_PACKS: tuple[VisualPack, ...] = (
    VisualPack("complementary-reimagined", "HVnmMxH1", "Complementary Reimagined", "shader"),
    VisualPack("complementary-unbound", "R6NEzAwj", "Complementary Unbound", "shader"),
)
DEFAULT_SHADER = "complementary-reimagined"


def install_visual_set(
    instance: Path,
    *,
    repository: ModRepository | None = None,
    game_version: str = TARGET_GAME_VERSION,
) -> dict[str, Any]:
    instance = Path(instance)
    repo = repository or ModrinthRepository(timeout=120)
    placed: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for pack in (*RESOURCE_PACKS, *SHADER_PACKS):
        folder = instance / ("resourcepacks" if pack.kind == "resourcepack" else "shaderpacks")
        folder.mkdir(parents=True, exist_ok=True)
        try:
            versions = repo.get_versions(pack.project, game_version=game_version)
        except ModRepositoryError as exc:
            skipped.append({"key": pack.key, "title": pack.title, "reason": exc.message})
            continue
        chosen = _pick(versions, game_version, pack.kind)
        if chosen is None:
            skipped.append({"key": pack.key, "title": pack.title, "reason": f"No {game_version} build."})
            continue
        version, file = chosen
        name = SAFE.sub("_", Path(file.filename).name).strip(" ._") or f"{pack.key}.zip"
        target = folder / name
        if not (target.is_file() and _matches(target.read_bytes(), file)):
            try:
                payload = repo.download_file(file.url)
            except ModRepositoryError as exc:
                skipped.append({"key": pack.key, "title": pack.title, "reason": exc.message})
                continue
            if not payload or not _matches(payload, file):
                skipped.append({"key": pack.key, "title": pack.title, "reason": "Hash mismatch."})
                continue
            target.write_bytes(payload)
        placed[pack.key] = {
            "title": pack.title,
            "kind": pack.kind,
            "versionNumber": version.version_number,
            "filename": name,
        }
    order = [placed[p.key]["filename"] for p in RESOURCE_PACKS if p.key in placed]
    options_changed = enable_resource_packs(instance / "options.txt", order)
    shader = placed.get(DEFAULT_SHADER)
    iris_changed = set_iris_shader(instance / "config" / "iris.properties", shader["filename"]) if shader else False
    gamma_seeded = seed_gamma_utils(instance / "config" / "gammautils.json")
    (instance / "resourcepacks").mkdir(parents=True, exist_ok=True)
    (instance / "resourcepacks" / MANIFEST_NAME).write_text(
        json.dumps({"placed": placed, "skipped": skipped}, indent=2), encoding="utf-8"
    )
    return {
        "ok": True,
        "placed": placed,
        "skipped": skipped,
        "optionsUpdated": options_changed,
        "irisUpdated": iris_changed,
        "gammaUtilsSeeded": gamma_seeded,
    }


def seed_gamma_utils(path: Path) -> bool:
    """First run only: start Gamma Utils with Night Vision on at 100%.

    Shaders ignore gamma, but Iris passes the night-vision strength to the
    shader pack, so this is the fullbright that works with Complementary.
    An existing gammautils.json (the player's own settings) is never touched.
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"nightVision": {"enabled": True, "value": 100.0, "toggledNightVision": 100}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return True


def enable_resource_packs(options: Path, filenames: list[str]) -> bool:
    """Put file/<pack> entries at the top of resourcePacks, in the given bottom->top order.

    Only edits an existing options.txt. Other enabled packs stay, below ours.
    """
    if not filenames or not options.is_file():
        return False
    lines = options.read_text(encoding="utf-8").splitlines()
    ours = [f"file/{name}" for name in filenames]
    found = False
    for index, line in enumerate(lines):
        if line.startswith("resourcePacks:"):
            found = True
            try:
                current = json.loads(line.split(":", 1)[1])
            except ValueError:
                current = ["vanilla"]
            if not isinstance(current, list):
                current = ["vanilla"]
            kept = [str(item) for item in current if str(item) not in ours]
            lines[index] = "resourcePacks:" + json.dumps(kept + ours, separators=(",", ":"))
        elif line.startswith("incompatibleResourcePacks:"):
            try:
                current = json.loads(line.split(":", 1)[1])
            except ValueError:
                current = []
            kept = [str(item) for item in current if str(item) not in ours] if isinstance(current, list) else []
            lines[index] = "incompatibleResourcePacks:" + json.dumps(kept, separators=(",", ":"))
    if not found:
        lines.append("resourcePacks:" + json.dumps(["vanilla", *ours], separators=(",", ":")))
    options.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def set_iris_shader(path: Path, filename: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    values: dict[str, str] = {}
    order: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key not in values:
                    order.append(key)
                values[key] = value
    for key, value in (("shaderPack", filename), ("enableShaders", "true")):
        if key not in values:
            order.append(key)
        values[key] = value
    path.write_text(
        "#Iris configuration (set by Vanta visual set)\n" + "".join(f"{k}={values[k]}\n" for k in order),
        encoding="utf-8",
    )
    return True


def _pick(versions: list[ModVersion], game_version: str, kind: str) -> tuple[ModVersion, ModFile] | None:
    for version in versions:
        if game_version not in {str(v) for v in version.game_versions}:
            continue
        loaders = {str(l).lower() for l in version.loaders}
        if kind == "shader" and loaders and "iris" not in loaders:
            continue
        files = [
            f for f in version.files
            if f.filename.lower().endswith(".zip")
            and (urlparse(f.url).hostname or "").endswith("modrinth.com")
        ]
        if files:
            return version, next((f for f in files if f.primary), files[0])
    return None


def _matches(payload: bytes, file: ModFile) -> bool:
    hashes = {str(k).lower(): str(v).lower() for k, v in (file.hashes or {}).items()}
    if hashes.get("sha1"):
        return hashlib.sha1(payload).hexdigest() == hashes["sha1"]
    if hashes.get("sha512"):
        return hashlib.sha512(payload).hexdigest() == hashes["sha512"]
    return bool(payload)
