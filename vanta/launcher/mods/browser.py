"""Vanta mod browser: Modrinth public API (https://api.modrinth.com/v2) only.

Rows (Today's Specials, Essentials, ...), search with filters, project details,
and one-click installs into an instance folder with required dependencies.
Responses are cached in memory and on disk; every request sends a Vanta User-Agent.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from vanta import __version__

API = "https://api.modrinth.com/v2"
USER_AGENT = f"addu864/vanta/{__version__} (Minecraft launcher; github.com/addu864/vanta)"
INDEX_NAME = "vanta-browser.json"
FOLDERS = {"mod": "mods", "shader": "shaderpacks", "resourcepack": "resourcepacks"}
SAFE_NAME = re.compile(r"[^\w.+()\- ]")

# Curated rows (Modrinth slugs). Missing builds for a version are skipped silently.
# Verified 1.21.11 Fabric slugs (vanta-mods-review.md, 2026-10-13).
ESSENTIALS = ["fabric-api", "sodium", "iris", "modmenu", "cloth-config", "yacl", "architectury-api",
              "fabric-language-kotlin", "placeholder-api", "no-chat-reports", "jade", "xaeros-minimap", "xaeros-world-map"]
QUALITY_OF_LIFE = ["appleskin", "mouse-tweaks", "inventory-profiles-next", "shulkerboxtooltip", "rei", "jei", "controlling",
                   "zoomify", "continuity", "betterf3", "3dskinlayers", "journeymap", "sound-physics-remastered",
                   "lambdynamiclights", "gamma-utils", "bobby"]
PERFORMANCE = ["sodium", "lithium", "ferrite-core", "entityculling", "immediatelyfast", "c2me-fabric", "krypton",
               "scalablelux", "moreculling", "badoptimizations", "dynamic-fps", "distanthorizons", "voxy"]
# Pairs that crash or fight when both are present (on top of the API's "incompatible" dependencies).
KNOWN_CONFLICTS = [("lambdynamiclights", "ryoamiclights"), ("lambdynamiclights", "sodium-dynamic-lights"),
                   ("sodium", "optifabric"), ("scalablelux", "starlight")]

ROWS = [
    ("specials", "Today's Specials"), ("essentials", "Essential mods"), ("qol", "Quality of Life"),
    ("performance", "Performance"), ("shaders", "Shaders"), ("resourcepacks", "Resource Packs"),
    ("top", "Top Mods"), ("new", "New & Updated"),
]


def load_curated(path: Path) -> dict[str, list[str]]:
    """Optional override file: lines like 'essentials: sodium, lithium'."""
    out: dict[str, list[str]] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"\s*[-*]?\s*(essentials|qol|performance)\s*:\s*(.+)$", line, re.I)
        if m:
            slugs = [s.strip(" `") for s in m.group(2).split(",") if re.fullmatch(r"[\w\-]+", s.strip(" `"))]
            if slugs:
                out[m.group(1).lower()] = slugs
    return out


class BrowserError(Exception):
    pass


class ModBrowser:
    def __init__(self, cache_dir: Path, *, opener: Callable[..., Any] | None = None, timeout: float = 20.0,
                 curated_file: Path | None = None) -> None:
        self.cache_dir = Path(cache_dir)
        self._opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self._mem: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.download_attempts = 3
        self.retry_delay = 1.5
        self.curated = {"essentials": ESSENTIALS, "qol": QUALITY_OF_LIFE, "performance": PERFORMANCE}
        if curated_file is not None:
            self.curated.update(load_curated(curated_file))

    # ---- HTTP with cache ----
    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{API}{path}{query}"
        data = json.dumps(body).encode() if body is not None else None
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise BrowserError(f"Modrinth answered HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BrowserError(f"Can't reach Modrinth right now ({getattr(exc, 'reason', exc)}).") from exc
        except ValueError as exc:
            raise BrowserError("Modrinth returned invalid JSON.") from exc

    def cached(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None,
               ttl: float = 1800) -> Any:
        key = hashlib.sha1(json.dumps([method, path, params, body], sort_keys=True).encode()).hexdigest()
        now = time.time()
        with self._lock:
            hit = self._mem.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        disk = self.cache_dir / f"{key}.json"
        try:
            if disk.is_file() and now - disk.stat().st_mtime < ttl:
                value = json.loads(disk.read_text(encoding="utf-8"))
                with self._lock:
                    self._mem[key] = (disk.stat().st_mtime, value)
                return value
        except (OSError, ValueError):
            pass
        try:
            value = self._request(method, path, params, body)
        except BrowserError:
            if disk.is_file():  # stale is better than nothing when offline
                try:
                    return json.loads(disk.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            raise
        with self._lock:
            self._mem[key] = (now, value)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = disk.with_suffix(".tmp")
            tmp.write_text(json.dumps(value), encoding="utf-8")
            tmp.replace(disk)
        except OSError:
            pass
        return value

    # ---- search / rows ----
    @staticmethod
    def _facets(project_type: str, game_version: str, loader: str, category: str = "") -> str:
        facets = [[f"project_type:{project_type}"]]
        if game_version:
            facets.append([f"versions:{game_version}"])
        if project_type == "mod" and loader:
            facets.append([f"categories:{loader}"])
        if category:
            facets.append([f"categories:{category}"])
        return json.dumps(facets)

    def search(self, query: str = "", *, game_version: str, loader: str = "fabric", project_type: str = "mod",
               category: str = "", index: str = "relevance", limit: int = 24, offset: int = 0) -> list[dict[str, Any]]:
        if project_type not in FOLDERS:
            project_type = "mod"
        if index not in {"relevance", "downloads", "follows", "newest", "updated"}:
            index = "relevance"
        category = category if re.fullmatch(r"[\w\-]{0,40}", category or "") else ""
        params = {"query": (query or "")[:100], "facets": self._facets(project_type, game_version, loader, category),
                  "index": index, "limit": str(max(1, min(int(limit), 100))), "offset": str(max(0, int(offset)))}
        data = self.cached("GET", "/search", params, ttl=1800) or {}
        return [card(h) for h in data.get("hits") or [] if isinstance(h, dict)]

    def modpacks(self, query: str = "", game_version: str = "", limit: int = 20) -> list[dict[str, Any]]:
        """Fabric modpacks on Modrinth (newest Minecraft first when no version is given)."""
        facets = [["project_type:modpack"], ["categories:fabric"]]
        if game_version:
            facets.append([f"versions:{game_version}"])
        params = {"query": (query or "")[:100], "facets": json.dumps(facets), "index": "relevance" if query else "downloads",
                  "limit": str(max(1, min(int(limit), 50)))}
        data = self.cached("GET", "/search", params, ttl=1800) or {}
        out = []
        for h in data.get("hits") or []:
            if isinstance(h, dict):
                c = card(h)
                c["gameVersions"] = [v for v in (h.get("versions") or []) if isinstance(v, str)][-6:]
                out.append(c)
        return out

    def modpack_file(self, project: str, in_range: Callable[[str], bool]) -> dict[str, Any]:
        """Newest Fabric .mrpack of a project for a Minecraft version Vanta supports."""
        if not re.fullmatch(r"[\w\-]{1,64}", project or ""):
            raise BrowserError("Unknown modpack.")
        versions = self.cached("GET", f"/project/{project}/version", {"loaders": json.dumps(["fabric"])}, ttl=900) or []
        for v in versions:
            games = [g for g in v.get("game_versions") or [] if in_range(g)]
            if not games:
                continue
            f = next((f for f in v.get("files") or [] if str(f.get("filename", "")).endswith(".mrpack") and f.get("primary")), None) \
                or next((f for f in v.get("files") or [] if str(f.get("filename", "")).endswith(".mrpack")), None)
            if f and str(f.get("url", "")).startswith("https://cdn.modrinth.com/"):
                return {"url": f["url"], "sha512": (f.get("hashes") or {}).get("sha512", ""), "name": v.get("name") or "",
                        "version": v.get("version_number") or "", "minecraft": games[0], "size": f.get("size") or 0}
        raise BrowserError("This modpack has no Fabric build for Minecraft 1.18 to 26.3.")

    def curated_row(self, slugs: list[str], game_version: str, loader: str) -> list[dict[str, Any]]:
        data = self.cached("GET", "/projects", {"ids": json.dumps(slugs)}, ttl=6 * 3600) or []
        data = self._with_authors([p for p in data if isinstance(p, dict)])
        by_slug = {p.get("slug"): p for p in data}
        out = []
        for slug in slugs:
            p = by_slug.get(slug)
            if not p:
                continue
            if game_version and game_version not in (p.get("game_versions") or []):
                continue
            if p.get("project_type") == "mod" and loader and loader not in (p.get("loaders") or []):
                continue
            out.append(card(p))
        return out

    def _with_authors(self, projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """/project responses carry a team id, not an author; batch-look up team owners."""
        teams = sorted({str(p.get("team")) for p in projects if p.get("team") and not p.get("author")})
        if teams:
            try:
                data = self.cached("GET", "/teams", {"ids": json.dumps(teams)}, ttl=24 * 3600) or []
            except BrowserError:
                data = []
            owners = {}
            for members in data:
                for m in members if isinstance(members, list) else []:
                    if m.get("is_owner") or m.get("role") == "Owner" or m.get("team_id") not in owners:
                        owners[m.get("team_id")] = ((m.get("user") or {}).get("username") or "")
            for p in projects:
                p.setdefault("author", owners.get(p.get("team"), ""))
        return projects

    def specials(self, game_version: str, loader: str, day: _dt.date | None = None) -> list[dict[str, Any]]:
        pool = [c for c in self.search(game_version=game_version, loader=loader, index="updated", limit=100)
                if c["downloads"] >= 50_000]
        pool += [c for c in self.search(game_version=game_version, loader=loader, index="follows", limit=40)
                 if c["id"] not in {p["id"] for p in pool}]
        day = day or _dt.date.today()
        rng = random.Random(int(day.strftime("%Y%m%d")))
        rng.shuffle(pool)
        return pool[:10]

    def row(self, key: str, game_version: str, loader: str = "fabric") -> list[dict[str, Any]]:
        if key == "specials":
            return self.specials(game_version, loader)
        if key in self.curated:
            return self.curated_row(self.curated[key], game_version, loader)
        if key == "shaders":
            return self.search(game_version=game_version, project_type="shader", index="downloads", limit=20)
        if key == "resourcepacks":
            return self.search(game_version=game_version, project_type="resourcepack", index="downloads", limit=20)
        if key == "top":
            return self.search(game_version=game_version, loader=loader, index="downloads", limit=20)
        if key == "new":
            return self.search(game_version=game_version, loader=loader, index="updated", limit=20)
        raise BrowserError("Unknown row.")

    def rows(self, game_version: str, loader: str = "fabric") -> list[dict[str, Any]]:
        out = []
        for key, title in ROWS:
            try:
                items, error = self.row(key, game_version, loader), ""
            except BrowserError as exc:
                items, error = [], str(exc)
            out.append({"key": key, "title": title, "items": items, "error": error})
        return out

    def categories(self) -> list[dict[str, str]]:
        data = self.cached("GET", "/tag/category", ttl=24 * 3600) or []
        return [{"name": c.get("name"), "type": c.get("project_type")} for c in data
                if isinstance(c, dict) and c.get("header") == "categories"]

    # ---- details ----
    def versions(self, project: str, game_version: str, project_type: str, loader: str = "fabric") -> list[dict[str, Any]]:
        params: dict[str, str] = {}
        if game_version:
            params["game_versions"] = json.dumps([game_version])
        if project_type == "mod":
            params["loaders"] = json.dumps([loader])
        elif project_type == "resourcepack":
            params["loaders"] = json.dumps(["minecraft"])
        data = self.cached("GET", f"/project/{urllib.parse.quote(project)}/version", params, ttl=900) or []
        if project_type == "shader":
            data = [v for v in data if set(v.get("loaders") or []) & {"iris", "optifine"}] or data
        return [v for v in data if isinstance(v, dict)]

    def details(self, project: str, game_version: str, loader: str = "fabric") -> dict[str, Any]:
        if not re.fullmatch(r"[\w\-]{1,64}", project or ""):
            raise BrowserError("Unknown project.")
        p = self.cached("GET", f"/project/{project}", ttl=3600)
        if not p:
            raise BrowserError("That project is not on Modrinth.")
        info = card(self._with_authors([dict(p)])[0])
        vers = self.versions(p["id"], game_version, info["type"], loader)
        dep_ids = sorted({d["project_id"] for v in vers[:1] for d in v.get("dependencies") or []
                          if d.get("project_id") and d.get("dependency_type") in {"required", "optional"}})
        deps = []
        if dep_ids:
            kinds = {d["project_id"]: d.get("dependency_type") for d in vers[0].get("dependencies") or [] if d.get("project_id")}
            for dp in self._with_authors(list(self.cached("GET", "/projects", {"ids": json.dumps(dep_ids)}, ttl=6 * 3600) or [])):
                deps.append({**card(dp), "required": kinds.get(dp.get("id")) == "required"})
        info.update({
            "body": clean_body(str(p.get("body") or "")),
            "gallery": [{"url": g.get("url"), "title": g.get("title") or ""} for g in p.get("gallery") or []
                        if isinstance(g, dict) and str(g.get("url") or "").startswith("https://")][:12],
            "links": {k: p.get(k) for k in ("source_url", "wiki_url", "discord_url", "issues_url")
                      if str(p.get(k) or "").startswith("https://")},
            "url": f"https://modrinth.com/{info['type']}/{info['slug']}",
            "versions": [{"id": v.get("id"), "name": v.get("name"), "number": v.get("version_number"),
                          "type": v.get("version_type"), "date": v.get("date_published"),
                          "gameVersions": v.get("game_versions") or []} for v in vers[:15]],
            "dependencies": deps,
        })
        return info

    # ---- installed index ----
    @staticmethod
    def index_path(instance: Path) -> Path:
        return instance / INDEX_NAME

    def read_index(self, instance: Path) -> dict[str, Any]:
        try:
            data = json.loads(self.index_path(instance).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_index(self, instance: Path, data: dict[str, Any]) -> None:
        path = self.index_path(instance)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def installed(self, instance: Path, game_version: str, loader: str = "fabric", check_updates: bool = False) -> dict[str, Any]:
        """Map project id -> install record; also recognises jars/packs placed by other means (sha1 lookup)."""
        index = self.read_index(instance)
        known_files = {(r.get("folder"), r.get("file")) for r in index.values()}
        hashes: dict[str, tuple[str, str]] = {}
        for folder in FOLDERS.values():
            d = instance / folder
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.is_file() and f.name.endswith((".jar", ".zip", ".jar.disabled")) and (folder, f.name) not in known_files:
                    try:
                        hashes[sha1_file(f)] = (folder, f.name)
                    except OSError:
                        pass
        if hashes:
            try:
                found = self.cached("POST", "/version_files", body={"hashes": sorted(hashes), "algorithm": "sha1"}, ttl=24 * 3600) or {}
            except BrowserError:
                found = {}
            for h, v in found.items():
                if h in hashes and isinstance(v, dict) and v.get("project_id") not in index:
                    folder, name = hashes[h]
                    index[v["project_id"]] = {"file": name, "folder": folder, "versionId": v.get("id"),
                                              "sha1": h, "detected": True}
        # drop records whose file vanished
        index = {k: r for k, r in index.items() if (instance / str(r.get("folder")) / str(r.get("file"))).is_file()}
        if check_updates and index:
            by_hash = {r.get("sha1"): pid for pid, r in index.items() if r.get("sha1")}
            try:
                latest = self.cached("POST", "/version_files/update", body={
                    "hashes": sorted(by_hash), "algorithm": "sha1", "loaders": [loader, "iris", "optifine", "minecraft"],
                    "game_versions": [game_version]}, ttl=1800) or {}
            except BrowserError:
                latest = {}
            for h, v in latest.items():
                pid = by_hash.get(h)
                if pid and isinstance(v, dict) and v.get("id") and v.get("id") != index[pid].get("versionId"):
                    index[pid]["update"] = {"versionId": v["id"], "number": v.get("version_number")}
        return index

    # ---- install / remove ----
    def _pick_version(self, project_id: str, game_version: str, project_type: str, loader: str, version_id: str = "") -> dict[str, Any] | None:
        if version_id:
            v = self.cached("GET", f"/version/{urllib.parse.quote(version_id)}", ttl=3600)
            return v if isinstance(v, dict) else None
        vers = self.versions(project_id, game_version, project_type, loader)
        releases = [v for v in vers if v.get("version_type") == "release"]
        return (releases or vers or [None])[0]

    def _download(self, url: str, hashes: dict[str, str]) -> bytes:
        host = urllib.parse.urlparse(url).hostname or ""
        if not url.startswith("https://") or not (host == "modrinth.com" or host.endswith(".modrinth.com")):
            raise BrowserError("Refusing a download that is not hosted on Modrinth.")
        last: BrowserError | None = None
        for attempt in range(self.download_attempts):
            if attempt:
                time.sleep(self.retry_delay * attempt)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with self._opener(req, timeout=120) as resp:
                    data = resp.read()
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = BrowserError(f"Download failed ({getattr(exc, 'reason', exc)}).")
                continue
            # A failed hash is usually a cut-off download: try again, never keep the bad file.
            if hashes.get("sha512") and hashlib.sha512(data).hexdigest() != hashes["sha512"]:
                last = BrowserError("Downloaded file failed its sha512 check.")
                continue
            if hashes.get("sha1") and hashlib.sha1(data).hexdigest() != hashes["sha1"]:
                last = BrowserError("Downloaded file failed its sha1 check.")
                continue
            return data
        raise last or BrowserError("Download failed.")

    def install(self, instance: Path, project: str, game_version: str, loader: str = "fabric", version_id: str = "",
                progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
        progress = progress or (lambda *_: None)
        index = self.installed(instance, game_version, loader)
        plan: list[tuple[dict[str, Any], dict[str, Any]]] = []
        seen: set[str] = set()
        skipped: list[str] = []

        def visit(pid: str, vid: str, top: bool) -> None:
            p = self.cached("GET", f"/project/{urllib.parse.quote(pid)}", ttl=3600)
            if not p:
                if top:
                    raise BrowserError("That project is not on Modrinth.")
                skipped.append(pid)
                return
            if p["id"] in seen or (not top and (p["id"] in index or _present(instance, p))):
                return
            seen.add(p["id"])
            ptype = p.get("project_type") if p.get("project_type") in FOLDERS else "mod"
            if ptype not in FOLDERS or p.get("project_type") not in FOLDERS:
                if top:
                    raise BrowserError("Vanta installs mods, shaders and resource packs only.")
                return
            v = self._pick_version(p["id"], game_version, ptype, loader, vid)
            if not v:
                if top:
                    raise BrowserError(f"{p.get('title')} has no build for Minecraft {game_version}.")
                skipped.append(str(p.get("title")))
                return
            plan.append((p, v))
            for d in v.get("dependencies") or []:
                if d.get("dependency_type") == "required" and d.get("project_id"):
                    visit(d["project_id"], d.get("version_id") or "", False)

        progress("Resolving dependencies", 0, 1)
        visit(project, version_id, True)
        present_slugs = {str(r.get("slug") or "") for r in index.values()}
        present_ids = set(index)
        for p, v in plan:
            for d in v.get("dependencies") or []:
                if d.get("dependency_type") == "incompatible" and d.get("project_id") in present_ids:
                    other = index[d["project_id"]].get("title") or index[d["project_id"]].get("file")
                    raise BrowserError(f"{p.get('title')} is incompatible with {other}, which is installed. Remove it first.")
            for a, b in KNOWN_CONFLICTS:
                for mine, theirs in ((a, b), (b, a)):
                    if p.get("slug") == mine and (theirs in present_slugs or _present(instance, {"slug": theirs, "project_type": "mod"})):
                        raise BrowserError(f"{p.get('title')} conflicts with {theirs}, which is installed. Remove it first.")
        total = len(plan)
        done = []
        for i, (p, v) in enumerate(plan):
            ptype = p["project_type"]
            files = [f for f in v.get("files") or [] if isinstance(f, dict)]
            f = next((f for f in files if f.get("primary")), files[0] if files else None)
            if not f:
                skipped.append(str(p.get("title")))
                continue
            progress(f"Downloading {p.get('title')}", i, total)
            data = self._download(str(f.get("url") or ""), f.get("hashes") or {})
            folder = FOLDERS[ptype]
            target_dir = instance / folder
            target_dir.mkdir(parents=True, exist_ok=True)
            name = SAFE_NAME.sub("_", Path(str(f.get("filename") or "file")).name).strip(" .") or f"{p['slug']}.jar"
            old = index.get(p["id"])
            if old and old.get("file") != name:
                for cand in (target_dir / str(old["file"]),):
                    try:
                        cand.unlink()
                    except OSError:
                        pass
            tmp = target_dir / (name + ".part")
            tmp.write_bytes(data)
            tmp.replace(target_dir / name)
            index[p["id"]] = {"file": name, "folder": folder, "versionId": v.get("id"), "number": v.get("version_number"),
                              "sha1": hashlib.sha1(data).hexdigest(), "title": p.get("title"), "slug": p.get("slug"),
                              "type": ptype, "installedAt": int(time.time())}
            done.append({"title": p.get("title"), "file": name, "folder": folder})
        progress("Done", total, total)
        self._write_index(instance, {k: {kk: vv for kk, vv in r.items() if kk != "update"} for k, r in index.items()})
        return {"ok": True, "installed": done, "skipped": skipped}

    def remove(self, instance: Path, project_id: str, game_version: str, loader: str = "fabric") -> dict[str, Any]:
        index = self.installed(instance, game_version, loader)
        rec = index.pop(project_id, None)
        if not rec:
            return {"ok": False, "error": "That item is not installed in this instance."}
        path = instance / str(rec.get("folder")) / str(rec.get("file"))
        if path.parent.name not in FOLDERS.values() or path.parent.parent != instance:
            return {"ok": False, "error": "Refusing to delete outside the instance."}
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            return {"ok": False, "error": f"Could not remove the file (is Minecraft still running?): {exc}"}
        self._write_index(instance, index)
        return {"ok": True, "removed": rec.get("file")}

    # ---- background jobs ----
    def start_install(self, instance: Path, project: str, game_version: str, loader: str = "fabric", version_id: str = "") -> str:
        job_id = hashlib.sha1(f"{project}{time.time()}".encode()).hexdigest()[:10]
        job = {"id": job_id, "project": project, "state": "running", "step": "Starting", "done": 0, "total": 1}
        self.jobs[job_id] = job

        def report(step: str, done: int, total: int) -> None:
            job.update(step=step, done=done, total=max(total, 1))

        def run() -> None:
            try:
                job["result"] = self.install(instance, project, game_version, loader, version_id, progress=report)
                job["state"] = "done"
            except BrowserError as exc:
                job.update(state="error", error=str(exc))
            except Exception as exc:  # noqa: BLE001
                job.update(state="error", error=f"Install failed: {exc}"[:300])

        threading.Thread(target=run, daemon=True).start()
        return job_id

    def update_all(self, instance: Path, game_version: str, loader: str = "fabric",
                   progress: Callable[[str, int, int], None] | None = None) -> dict[str, Any]:
        """Install every available update (hash-checked, one project at a time). One failure doesn't stop the rest."""
        progress = progress or (lambda *_: None)
        progress("Checking for updates", 0, 1)
        index = self.installed(instance, game_version, loader, check_updates=True)
        todo = [(pid, r) for pid, r in index.items() if r.get("update")]
        updated, failed = [], []
        for i, (pid, rec) in enumerate(todo):
            label = str(rec.get("title") or rec.get("file") or pid)
            progress(f"Updating {label}", i, len(todo))
            try:
                self.install(instance, pid, game_version, loader, str(rec["update"]["versionId"]))
                updated.append({"title": label, "number": rec["update"].get("number")})
            except BrowserError as exc:
                failed.append({"title": label, "error": str(exc)})
        progress("Done", len(todo), max(len(todo), 1))
        return {"updated": updated, "failed": failed, "checked": len(index)}

    def start_update_all(self, instance: Path, game_version: str, loader: str = "fabric") -> str:
        job_id = hashlib.sha1(f"update-all{time.time()}".encode()).hexdigest()[:10]
        job = {"id": job_id, "project": "*", "state": "running", "step": "Starting", "done": 0, "total": 1}
        self.jobs[job_id] = job

        def report(step: str, done: int, total: int) -> None:
            job.update(step=step, done=done, total=max(total, 1))

        def run() -> None:
            try:
                job["result"] = self.update_all(instance, game_version, loader, progress=report)
                job["state"] = "done"
            except Exception as exc:  # noqa: BLE001
                job.update(state="error", error=f"Update failed: {exc}"[:300])

        threading.Thread(target=run, daemon=True).start()
        return job_id


def _present(instance: Path, p: dict[str, Any]) -> bool:
    """A dependency counts as present when a file in its folder carries the project's slug."""
    folder = instance / FOLDERS.get(str(p.get("project_type")), "mods")
    slug = re.sub(r"[-_ ]", "", str(p.get("slug") or "").lower())
    if not slug or not folder.is_dir():
        return False
    return any(re.sub(r"[-_ ]", "", f.name.lower()).startswith(slug) for f in folder.iterdir())


def card(p: dict[str, Any]) -> dict[str, Any]:
    icon = p.get("icon_url")
    ptype = p.get("project_type") or "mod"
    return {
        "id": str(p.get("project_id") or p.get("id") or ""),
        "slug": str(p.get("slug") or ""),
        "title": str(p.get("title") or p.get("slug") or "Untitled"),
        "author": str(p.get("author") or ""),
        "description": str(p.get("description") or "")[:300],
        "icon": icon if isinstance(icon, str) and icon.startswith("https://") else None,
        "downloads": int(p.get("downloads") or 0),
        "follows": int(p.get("follows") or p.get("followers") or 0),
        "type": ptype if ptype in FOLDERS else str(ptype),
        "categories": [str(c) for c in (p.get("display_categories") or p.get("categories") or [])][:6],
        "updated": p.get("date_modified") or p.get("updated"),
        "banner": next((u for u in [p.get("featured_gallery"), p.get("gallery", [None])[0] if isinstance(p.get("gallery"), list) and p.get("gallery") and isinstance(p.get("gallery")[0], str) else None] if isinstance(u, str) and u.startswith("https://")), None),
    }


_TAG = re.compile(r"<[^>]+>")
_IMG = re.compile(r"<img\b[^>]*\bsrc=[\"'](https://[^\"']+)[\"'][^>]*>", re.I)


def clean_body(body: str) -> str:
    """Markdown with raw HTML removed (img tags become markdown images). The UI escapes everything."""
    body = _IMG.sub(lambda m: f"\n![]({m.group(1)})\n", body[:60000])
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = _TAG.sub("", body)
    return html.unescape(body).strip()


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
