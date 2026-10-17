"""Modrinth modpacks (.mrpack): import into an instance, and export an instance as a .mrpack.

Format: https://support.modrinth.com/en/articles/8802351-modrinth-modpack-format-mrpack
Only Fabric packs are supported (Vanta's mod loader). Every file is checked:
relative paths only, downloads only from Modrinth/GitHub/GitLab hosts, SHA-1 and SHA-512 must match.
Before a pack is applied, the instance's current mods/config/packs are moved to a
vanta-backup-<time> folder, never deleted.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from vanta import __version__

ALLOWED_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
USER_AGENT = f"addu864/vanta/{__version__} (Minecraft launcher; github.com/addu864/vanta)"
MANAGED = ("mods", "config", "resourcepacks", "shaderpacks", "options.txt")
MAX_PACK_BYTES = 1024 * 1024 * 1024
MAX_FILES = 800


class PackError(ValueError):
    pass


def safe_rel(path: str) -> PurePosixPath:
    p = PurePosixPath(str(path or "").replace("\\", "/"))
    if not str(p) or p.is_absolute() or any(part in {"..", ""} for part in p.parts) or ":" in p.parts[0]:
        raise PackError(f"Unsafe path in modpack: {path}")
    return p


def read_index(pack: zipfile.ZipFile) -> dict[str, Any]:
    try:
        index = json.loads(pack.read("modrinth.index.json"))
    except KeyError:
        raise PackError("This isn't a Modrinth modpack (no modrinth.index.json).") from None
    except ValueError:
        raise PackError("The modpack's modrinth.index.json is damaged.") from None
    if not isinstance(index, dict) or index.get("formatVersion") != 1 or index.get("game") != "minecraft":
        raise PackError("Unsupported modpack format (Vanta reads formatVersion 1 Minecraft packs).")
    deps = index.get("dependencies") or {}
    mc = str(deps.get("minecraft") or "")
    if not re.fullmatch(r"[0-9][0-9A-Za-z.\-_]{0,31}", mc):
        raise PackError("The modpack doesn't say which Minecraft version it needs.")
    others = [k for k in deps if k not in {"minecraft", "fabric-loader"}]
    if others:
        raise PackError(f"This pack needs {', '.join(others)}. Vanta runs Fabric packs only.")
    files = []
    for f in index.get("files") or []:
        if not isinstance(f, dict):
            continue
        if ((f.get("env") or {}).get("client")) == "unsupported":
            continue
        rel = safe_rel(f.get("path"))
        hashes = f.get("hashes") or {}
        if not re.fullmatch(r"[0-9a-f]{40}", str(hashes.get("sha1") or "")) or not re.fullmatch(r"[0-9a-f]{128}", str(hashes.get("sha512") or "")):
            raise PackError(f"{rel.name} has no SHA-1/SHA-512 hash in the pack.")
        urls = [u for u in f.get("downloads") or [] if isinstance(u, str) and urllib.parse.urlsplit(u).scheme == "https"
                and urllib.parse.urlsplit(u).hostname in ALLOWED_HOSTS]
        if not urls:
            raise PackError(f"{rel.name} isn't hosted on Modrinth, GitHub or GitLab, so Vanta won't download it.")
        files.append({"path": str(rel), "sha1": hashes["sha1"], "sha512": hashes["sha512"], "urls": urls,
                      "size": int(f.get("fileSize") or 0)})
    if len(files) > MAX_FILES:
        raise PackError("This modpack has too many files.")
    return {"name": str(index.get("name") or "Modpack")[:80], "version": str(index.get("versionId") or "")[:40],
            "summary": str(index.get("summary") or "")[:300], "minecraft": mc,
            "fabricLoader": str(deps.get("fabric-loader") or ""), "loader": "fabric" if "fabric-loader" in deps else "vanilla",
            "files": files}


def http_get(url: str, timeout: int = 60) -> bytes:
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise PackError(f"Download failed: {last}")


def _verify(data: bytes, sha1: str, sha512: str) -> bool:
    return hashlib.sha1(data).hexdigest() == sha1 and hashlib.sha512(data).hexdigest() == sha512


def backup_managed(instance: Path) -> str:
    """Move mods/config/packs/options.txt out of the way. Returns the backup folder name ('' if nothing moved)."""
    moved = []
    dest = instance / f"vanta-backup-{time.strftime('%Y%m%d-%H%M%S')}"
    for name in MANAGED:
        src = instance / name
        if src.exists():
            dest.mkdir(exist_ok=True)
            shutil.move(str(src), str(dest / name))
            moved.append(name)
    return dest.name if moved else ""


def _extract_overrides(pack: zipfile.ZipFile, instance: Path) -> int:
    count = 0
    root = instance.resolve()
    for prefix in ("overrides/", "client-overrides/"):
        for info in pack.infolist():
            name = info.filename.replace("\\", "/")
            if not name.startswith(prefix) or info.is_dir() or name == prefix:
                continue
            rel = safe_rel(name[len(prefix):])
            out = (root / Path(*rel.parts)).resolve()
            if not out.is_relative_to(root):
                raise PackError(f"Unsafe path in modpack: {name}")
            out.parent.mkdir(parents=True, exist_ok=True)
            with pack.open(info) as src, out.open("wb") as fh:
                shutil.copyfileobj(src, fh)
            count += 1
    return count


def http_download(url: str, dest: Path, timeout: int = 120) -> Path:
    """Stream a file to disk (3 tries)."""
    last: Exception | None = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as fh:
                shutil.copyfileobj(resp, fh, 1 << 20)
            return dest
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise PackError(f"Download failed: {last}")


def apply_pack(instance: Path, pack: bytes | Path, *, fetch: Callable[[str], bytes] = http_get,
               progress: Callable[[int, int], None] | None = None, workers: int = 6) -> dict[str, Any]:
    """Download + verify every file into a staging folder first; only then touch the instance."""
    from concurrent.futures import ThreadPoolExecutor

    instance = Path(instance)
    size = len(pack) if isinstance(pack, (bytes, bytearray)) else Path(pack).stat().st_size
    if size > MAX_PACK_BYTES:
        raise PackError("That modpack file is too large.")
    source = io.BytesIO(pack) if isinstance(pack, (bytes, bytearray)) else Path(pack)
    with zipfile.ZipFile(source) as zf:
        info = read_index(zf)
        instance.mkdir(parents=True, exist_ok=True)
        staging = instance / ".vanta-pack-staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        done = [0]
        total = len(info["files"])

        def one(item: tuple[int, dict[str, Any]]) -> None:
            i, f = item
            for url in f["urls"]:
                try:
                    data = fetch(url)
                except PackError:
                    continue
                if _verify(data, f["sha1"], f["sha512"]):
                    (staging / str(i)).write_bytes(data)
                    done[0] += 1
                    if progress:
                        progress(done[0], total)
                    return
            raise PackError(f"{PurePosixPath(f['path']).name} failed to download or its hash didn't match.")

        try:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                list(pool.map(one, enumerate(info["files"])))
            backup = backup_managed(instance)
            root = instance.resolve()
            for i, f in enumerate(info["files"]):
                rel = PurePosixPath(f["path"])
                out = (root / Path(*rel.parts)).resolve()
                if not out.is_relative_to(root):
                    raise PackError(f"Unsafe path in modpack: {rel}")
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staging / str(i)), str(out))
            overrides = _extract_overrides(zf, instance)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    (instance / "vanta-modpack.json").write_text(json.dumps({k: info[k] for k in ("name", "version", "minecraft", "fabricLoader")}
                                                          | {"installedAt": int(time.time()), "backup": backup}, indent=2))
    return {"ok": True, "name": info["name"], "files": total, "overrides": overrides, "backup": backup}


# ---- export ----
def _hashes(path: Path) -> tuple[str, str]:
    h1, h5 = hashlib.sha1(), hashlib.sha512()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h1.update(chunk)
            h5.update(chunk)
    return h1.hexdigest(), h5.hexdigest()


def modrinth_lookup(sha1s: list[str]) -> dict[str, Any]:
    """POST /v2/version_files: which of these files are on Modrinth (by SHA-1)."""
    if not sha1s:
        return {}
    body = json.dumps({"hashes": sha1s, "algorithm": "sha1"}).encode()
    req = urllib.request.Request("https://api.modrinth.com/v2/version_files", data=body, method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return data if isinstance(data, dict) else {}


def export_pack(instance: Path, out_file: Path, *, name: str, minecraft: str, fabric_loader: str = "",
                lookup: Callable[[list[str]], dict[str, Any]] = modrinth_lookup) -> dict[str, Any]:
    """Files found on Modrinth are referenced by URL; anything else goes into overrides/."""
    instance = Path(instance)
    candidates: list[Path] = []
    for folder, exts in (("mods", (".jar",)), ("resourcepacks", (".zip",)), ("shaderpacks", (".zip",))):
        d = instance / folder
        if d.is_dir():
            candidates += sorted(p for p in d.iterdir() if p.is_file() and p.name.endswith(exts))
    hashed = {p: _hashes(p) for p in candidates}
    try:
        known = lookup([h[0] for h in hashed.values()])
    except Exception:  # noqa: BLE001 - offline: everything goes into overrides
        known = {}
    files, overrides = [], []
    for p, (sha1, sha512) in hashed.items():
        ver = known.get(sha1) or {}
        url = next((f.get("url") for f in ver.get("files") or [] if (f.get("hashes") or {}).get("sha1") == sha1), None)
        rel = p.relative_to(instance).as_posix()
        if url and urllib.parse.urlsplit(url).hostname in ALLOWED_HOSTS:
            files.append({"path": rel, "hashes": {"sha1": sha1, "sha512": sha512}, "env": {"client": "required", "server": "optional"},
                          "downloads": [url], "fileSize": p.stat().st_size})
        else:
            overrides.append(p)
    deps = {"minecraft": minecraft}
    if fabric_loader:
        deps["fabric-loader"] = fabric_loader
    index = {"formatVersion": 1, "game": "minecraft", "versionId": time.strftime("%Y.%m.%d"), "name": name[:80],
             "summary": f"Exported from Vanta {__version__}", "files": files, "dependencies": deps}
    out_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_file.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("modrinth.index.json", json.dumps(index, indent=2))
        for p in overrides:
            z.write(p, "overrides/" + p.relative_to(instance).as_posix())
        cfg = instance / "config"
        if cfg.is_dir():
            for f in sorted(cfg.rglob("*")):
                if f.is_file() and f.stat().st_size < 5 * 1024 * 1024:
                    z.write(f, "overrides/" + f.relative_to(instance).as_posix())
        if (instance / "options.txt").is_file():
            z.write(instance / "options.txt", "overrides/options.txt")
    tmp.replace(out_file)
    return {"ok": True, "file": str(out_file), "linked": len(files), "bundled": len(overrides),
            "sizeMb": round(out_file.stat().st_size / 1048576, 1)}
