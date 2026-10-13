"""Downloadable instances: Minecraft 1.18 through 26.3, Fabric or vanilla.

Versions come from Mojang's version manifest (releases only, and only ones
that exist). Fabric availability comes from Fabric meta. Each install goes to
its own folder ``<game>/<version>-fabric`` or ``<version>-vanilla``. Assets are
shared in ``<game>/assets``. The existing 1.21.11-fabric instance is never
touched by this module.
"""

from __future__ import annotations

import json
import re
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
# Download types shown as cards in Add instance.
FAST_MODS: tuple[tuple[str, str], ...] = (
    ("fabric-api", "Fabric API"),
    ("sodium", "Sodium"),
    ("lithium", "Lithium"),
    ("modernfix", "ModernFix"),
    ("immediatelyfast", "ImmediatelyFast"),
    ("entityculling", "Entity Culling"),
    ("ferrite-core", "FerriteCore"),
    ("modmenu", "Mod Menu"),
)
TRAILER_MODS: tuple[tuple[str, str], ...] = (
    ("iris", "Iris Shaders"),
    ("entity-model-features", "Entity Model Features"),
    ("entitytexturefeatures", "Entity Texture Features"),
    ("not-enough-animations", "Not Enough Animations"),
    ("visuality", "Visuality"),
    ("gamma-utils", "Gamma Utils"),
)
PRESETS: dict[str, dict[str, Any]] = {
    "vanilla": {"title": "Vanilla", "loader": "vanilla",
                "description": "Plain Minecraft exactly as Mojang ships it. No mod loader, no mods."},
    "fast": {"title": "Fast", "loader": "fabric",
             "description": "Fabric with performance mods: Fabric API, Sodium, Lithium, ModernFix, ImmediatelyFast, Entity Culling, FerriteCore and Mod Menu (where they exist for the version)."},
    "custom": {"title": "Custom", "loader": "fabric",
               "description": "Fabric plus mods you pick from Modrinth for that version. Optionally start from the Fast set."},
    "trailer": {"title": "Trailer style", "loader": "fabric",
                "description": "The Fast set plus Iris with Complementary shaders, Bare Bones + Fresh Animations packs, EMF/ETF, Not Enough Animations, Visuality and Gamma Utils night vision on B."},
}
# Gamma Utils renamed its key between versions; Minecraft ignores the one it doesn't know.
NIGHT_VISION_KEYS = ("key_key.gammautils.nightVisionToggle", "key_key.gamma_utils.night_vision_toggle")
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
    preset: str = "",
    extra_mods: list[str] | None = None,
    include_fast: bool = True,
    visual_installer: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if preset not in PRESETS:
        preset = "fast" if loader == "fabric" else "vanilla"
    loader = PRESETS[preset]["loader"]
    picks = [str(m).strip() for m in (extra_mods or []) if str(m).strip()][:60]
    if not in_range(version):
        return {"ok": False, "error": f"Version {version} is outside 1.18 to 26.3."}
    name = instance_name(version, loader)
    if name in PROTECTED:
        return {"ok": False, "error": f"{name} is Vanta's main instance and is already installed; it is not re-downloaded here."}
    with _jobs_lock:
        current = _jobs.get(name)
        if current and current.get("state") in {"queued", "downloading"}:
            return {"ok": True, "job": dict(current), "already": True}
        _jobs[name] = {"id": name, "version": version, "loader": loader, "preset": preset, "state": "queued", "step": "queued", "done": 0, "total": 1, "percent": 0, "error": ""}

    def update(**fields: Any) -> None:
        with _jobs_lock:
            _jobs[name].update(fields)

    weights = {"metadata": (0, 2), "client": (2, 8), "libraries": (8, 30), "fabric": (30, 35), "assets": (35, 88), "mods": (88, 94), "packs": (94, 99)}

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
            visual: dict[str, Any] = {}
            if loader == "fabric":
                progress("mods", 0, 1)
                slugs = preset_mod_list(preset, picks, include_fast)
                if mods_installer is not None:
                    mods = mods_installer(root / name / "mods", version)
                else:
                    mods = install_mod_list(root / name / "mods", version, slugs)
                if preset == "trailer":
                    visual = apply_trailer_visuals(root / name, version, installer=visual_installer,
                                                   progress=None if visual_installer else progress)
            meta = {
                "id": name,
                "versionNumber": version,
                "loader": loader,
                "fabricLoader": fabric_loader,
                "javaMajor": result.get("javaMajor"),
                "assetsRoot": str(result.get("assetsRoot") or root / "assets"),
                "defaultMods": [m.get("title") for m in mods.get("installed", []) + mods.get("kept", [])],
                "skippedMods": [f"{m.get('title')}: {m.get('reason')}" for m in mods.get("skipped", [])]
                + [f"{v.get('title')}: {v.get('reason')}" for v in visual.get("skipped", [])],
                "preset": preset,
                "trailer": preset == "trailer",
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


def preset_mod_list(preset: str, picks: list[str] | None = None, include_fast: bool = True) -> list[tuple[str, str]]:
    if preset == "vanilla":
        return []
    out: list[tuple[str, str]] = []
    if preset in {"fast", "trailer"} or (preset == "custom" and include_fast):
        out += list(FAST_MODS)
    elif preset == "custom":
        out.append(("fabric-api", "Fabric API"))
    if preset == "trailer":
        out += list(TRAILER_MODS)
    if preset == "custom":
        out += [(slug, slug) for slug in (picks or [])]
    seen: set[str] = set()
    return [m for m in out if not (m[0] in seen or seen.add(m[0]))]


def ensure_options(instance: Path) -> Path:
    """New instances have no options.txt yet; seed the safe defaults Vanta relies on."""
    options = Path(instance) / "options.txt"
    if not options.is_file():
        options.parent.mkdir(parents=True, exist_ok=True)
        options.write_text("toggleUse:false\ntoggleCrouch:false\nlang:en_us\n", encoding="utf-8")
    return options


def set_option(options: Path, key: str, value: str) -> None:
    lines = options.read_text(encoding="utf-8").splitlines() if options.is_file() else []
    lines = [line for line in lines if not line.startswith(key + ":")] + [f"{key}:{value}"]
    options.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_trailer_visuals(instance: Path, version: str, installer: Callable[..., dict[str, Any]] | None = None,
                          progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
    from vanta.launcher.mods import visual_set

    options = ensure_options(instance)
    for key in NIGHT_VISION_KEYS:
        set_option(options, key, "key.keyboard.b")
    run = installer or visual_set.install_visual_set
    if progress is not None:
        return run(Path(instance), game_version=version, progress=progress)
    return run(Path(instance), game_version=version)


TRAILER_FILE_HINTS = ("iris", "entity_model_features", "entity-model-features", "entity_texture_features",
                      "entitytexturefeatures", "notenoughanimations", "not-enough-animations", "visuality", "gamma-utils", "gammautils")


def set_trailer(instance: Path, version: str, on: bool, *, mods_runner: Callable[..., dict[str, Any]] | None = None,
                visual_installer: Callable[..., dict[str, Any]] | None = None,
                progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
    """Turn Trailer style on (download/enable mods + packs) or off (disable mods, unlist packs) for an instance."""
    from vanta.launcher.mods import visual_set

    instance = Path(instance)
    if instance.name in PROTECTED:
        return {"ok": False, "error": "The main 1.21.11 instance always has the trailer set; it is not toggled here."}
    if instance.name.endswith("-vanilla"):
        return {"ok": False, "error": "Trailer style needs Fabric. Vanilla instances can't load mods."}
    mods = instance / "mods"
    mods.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []
    for jar in list(mods.iterdir()):
        low = jar.name.lower()
        if not any(h in low for h in TRAILER_FILE_HINTS):
            continue
        if on and low.endswith(".jar.disabled"):
            jar.rename(jar.with_name(jar.name[: -len(".disabled")])); changed.append(jar.name)
        elif not on and low.endswith(".jar"):
            jar.rename(jar.with_name(jar.name + ".disabled")); changed.append(jar.name)
    result: dict[str, Any] = {"ok": True, "trailer": on, "changed": changed, "skipped": []}
    options = ensure_options(instance)
    if on:
        runner = mods_runner or install_mod_list
        if progress:
            progress("mods", 0, 1)
        mods_res = runner(mods, version, list(TRAILER_MODS))
        if progress:
            progress("mods", 1, 1)
        vis = apply_trailer_visuals(instance, version, installer=visual_installer, progress=progress)
        result["skipped"] = [f"{m.get('title')}: {m.get('reason')}" for m in mods_res.get("skipped", [])] + [
            f"{v.get('title')}: {v.get('reason')}" for v in vis.get("skipped", [])]
    else:
        lines = options.read_text(encoding="utf-8").splitlines()
        manifest = instance / "resourcepacks" / visual_set.MANIFEST_NAME
        names = []
        if manifest.is_file():
            names = [f"file/{v['filename']}" for v in json.loads(manifest.read_text(encoding="utf-8")).get("placed", {}).values() if v.get("kind") == "resourcepack"]
        for i, line in enumerate(lines):
            if line.startswith("resourcePacks:"):
                try:
                    cur = json.loads(line.split(":", 1)[1])
                except ValueError:
                    cur = ["vanilla"]
                lines[i] = "resourcePacks:" + json.dumps([x for x in cur if x not in names], separators=(",", ":"))
        options.write_text("\n".join(lines) + "\n", encoding="utf-8")
        iris = instance / "config" / "iris.properties"
        if iris.is_file():
            text = re.sub(r"(?m)^enableShaders=.*$", "", iris.read_text(encoding="utf-8")).rstrip() + "\nenableShaders=false\n"
            iris.write_text(text, encoding="utf-8")
    meta_path = instance / META_NAME
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["trailer"] = on
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return result


def install_default_mods(mods_dir: Path, game_version: str, repository: Any = None) -> dict[str, Any]:
    """Fabric API, Sodium, Lithium, Mod Menu for this version, where Modrinth has a build."""
    return install_mod_list(mods_dir, game_version, list(DEFAULT_MODS), repository)


def install_mod_list(mods_dir: Path, game_version: str, mods: list[tuple[str, str]], repository: Any = None) -> dict[str, Any]:
    """Place each (slug, title) for this version where Modrinth has a build; missing ones are skipped."""
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
    for slug, title in mods:
        required._place_one(
            repo, folder, mod_id=slug, slug=slug, title=title, game_version=game_version, loader="fabric",
            installed=installed, skipped=skipped, kept=kept, seen_projects=seen, extra=extra, previous={},
        )
    for mod_id, slug, title in extra:
        required._place_one(
            repo, folder, mod_id=mod_id, slug=slug, title=title, game_version=game_version, loader="fabric",
            installed=installed, skipped=skipped, kept=kept, seen_projects=seen, extra=None, previous={},
        )
    previous = folder / required.MANIFEST_NAME
    if previous.is_file():
        try:
            old = json.loads(previous.read_text(encoding="utf-8"))
            known = {str(i.get("filename")) for i in installed + kept}
            kept = kept + [i for i in (old.get("installed") or []) + (old.get("kept") or []) if str(i.get("filename")) not in known]
        except (OSError, ValueError):
            pass
    required._write_manifest(folder, installed, kept)
    return {"ok": True, "installed": installed, "kept": kept, "skipped": skipped}


# Live progress of a Trailer-style change, for the INSTANCES tab to poll.
_trailer_lock = threading.Lock()
_trailer_progress: dict[str, Any] = {"state": "idle"}


def trailer_progress() -> dict[str, Any]:
    with _trailer_lock:
        return dict(_trailer_progress)


def track_trailer(name: str) -> Callable[[str, int, int], None]:
    with _trailer_lock:
        _trailer_progress.clear()
        _trailer_progress.update(state="running", instance=name, step="mods", done=0, total=1, percent=0)
    weights = {"mods": (0, 40), "packs": (40, 100)}

    def report(step: str, done: int, total: int) -> None:
        lo, hi = weights.get(step, (0, 100))
        frac = (done / total) if total else 1.0
        with _trailer_lock:
            _trailer_progress.update(step=step, done=done, total=total, percent=int(lo + (hi - lo) * min(1.0, frac)))
    return report


def finish_trailer(ok: bool) -> None:
    with _trailer_lock:
        _trailer_progress.update(state="done" if ok else "failed", percent=100)
