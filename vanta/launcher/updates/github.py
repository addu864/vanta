"""Update check against GitHub Releases, and a hash-checked in-place code update.

The check reads the newest non-macOS release tag (``vX.Y.Z``). The update
downloads only the code and UI files (``vanta/`` and ``assets/``) that differ
from that tag, verifying each against its git blob SHA-1, writes them
atomically, and never touches the data folder, accounts, worlds or game mods.
The running app needs a restart afterwards.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from vanta import __version__

REPO = "addu864/vanta"
API = f"https://api.github.com/repos/{REPO}"
RAW = f"https://raw.githubusercontent.com/{REPO}"
CACHE_SECONDS = 1800
UPDATABLE = ("vanta/", "assets/")
SKIP = ("assets/backgrounds/custom",)

_cache: dict[str, Any] = {}
_lock = threading.Lock()
_job: dict[str, Any] = {"state": "idle"}


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": f"Vanta/{__version__}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"Vanta/{__version__}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def version_tuple(tag: str) -> tuple[int, ...]:
    m = re.match(r"v?(\d+(?:\.\d+)*)", tag or "")
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def latest_release(releases: list[dict[str, Any]]) -> dict[str, Any] | None:
    best = None
    for rel in releases:
        tag = str(rel.get("tag_name") or "")
        if rel.get("draft") or rel.get("prerelease") or not re.fullmatch(r"v\d+(\.\d+)*", tag):
            continue
        if best is None or version_tuple(tag) > version_tuple(best["tag_name"]):
            best = rel
    return best


def check_for_updates(fetch: Callable[[str], Any] = _get_json, current: str = __version__, force: bool = False) -> dict[str, Any]:
    now = time.time()
    with _lock:
        if not force and _cache.get("at") and now - _cache["at"] < CACHE_SECONDS:
            return dict(_cache["result"])
    try:
        rel = latest_release(fetch(f"{API}/releases?per_page=20"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "implemented": True, "current": current, "message": f"Could not reach GitHub: {exc}"[:200]}
    if rel is None:
        result = {"ok": True, "implemented": True, "current": current, "available": False, "message": "No releases found."}
    else:
        newer = version_tuple(rel["tag_name"]) > version_tuple(current)
        result = {
            "ok": True, "implemented": True, "current": current, "latest": rel["tag_name"], "available": newer,
            "name": rel.get("name") or rel["tag_name"], "url": rel.get("html_url"),
            "notes": str(rel.get("body") or "")[:3000],
            "message": f"Vanta {rel['tag_name']} is available." if newer else f"Vanta {current} is up to date.",
        }
    with _lock:
        _cache.update(at=now, result=result)
    return dict(result)


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def plan_update(root: Path, tree: list[dict[str, Any]]) -> list[dict[str, str]]:
    todo = []
    for item in tree:
        path = str(item.get("path") or "")
        if item.get("type") != "blob" or not path.startswith(UPDATABLE) or path.startswith(SKIP) or ".." in path:
            continue
        local = root / path
        if local.is_file() and git_blob_sha(local.read_bytes()) == item.get("sha"):
            continue
        todo.append({"path": path, "sha": str(item["sha"])})
    return todo


def apply_update(root: Path, tag: str, *, fetch_json: Callable[[str], Any] = _get_json,
                 fetch_bytes: Callable[[str], bytes] = _get_bytes, progress: Callable[[int, int], None] | None = None) -> dict[str, Any]:
    tree = fetch_json(f"{API}/git/trees/{tag}?recursive=1").get("tree") or []
    todo = plan_update(root, tree)
    done: list[str] = []
    failed: list[str] = []

    def one(item: dict[str, str]) -> None:
        data = None
        for _ in range(4):
            try:
                data = fetch_bytes(f"{RAW}/{tag}/{item['path']}")
                if git_blob_sha(data) == item["sha"]:
                    break
                data = None
            except Exception:  # noqa: BLE001
                data = None
                time.sleep(0.5)
        if data is None:
            failed.append(item["path"])
            return
        dest = root / item["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".vupd")
        tmp.write_bytes(data)
        tmp.replace(dest)
        done.append(item["path"])
        if progress:
            progress(len(done) + len(failed), len(todo))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one, todo))
    return {"ok": not failed, "tag": tag, "updated": sorted(done), "failed": sorted(failed), "checked": len(tree)}


def start_update(root: Path, tag: str) -> dict[str, Any]:
    with _lock:
        if _job.get("state") == "running":
            return dict(_job)
        _job.clear()
        _job.update(state="running", tag=tag, done=0, total=0, percent=0)

    def prog(d: int, t: int) -> None:
        with _lock:
            _job.update(done=d, total=t, percent=int(100 * d / t) if t else 100)

    def work() -> None:
        try:
            res = apply_update(root, tag, progress=prog)
            with _lock:
                _job.update(state="done" if res["ok"] else "failed", result=res, percent=100,
                            error="" if res["ok"] else f"{len(res['failed'])} files failed")
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _job.update(state="failed", error=str(exc)[:300])

    threading.Thread(target=work, name="vanta-update", daemon=True).start()
    return dict(_job)


def mark_logged() -> None:
    with _lock:
        _job["logged"] = True


def update_job() -> dict[str, Any]:
    with _lock:
        return dict(_job)
