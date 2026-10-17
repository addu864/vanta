"""Pre-launch mod checks: read each jar's own metadata and warn about problems that would
crash or break the game, before Java even starts. Read-only; never changes the mods folder."""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

# Provided by the loader / game itself.
BUILTIN = {"minecraft", "java", "fabricloader", "fabric-loader", "quilt_loader", "quilt_base", "mixinextras"}
# Known pairs that do not work together (both ids present -> warning).
KNOWN_CLASHES = [
    ({"optifabric", "optifine"}, {"sodium", "embeddium", "rubidium"}, "OptiFine and Sodium both replace the renderer; use one."),
    ({"optifabric", "optifine"}, {"iris", "oculus"}, "OptiFine and Iris both handle shaders; use one."),
    ({"sodium"}, {"embeddium", "rubidium"}, "Sodium and Embeddium are the same renderer; keep one."),
    ({"canvas"}, {"sodium", "iris"}, "Canvas replaces the renderer like Sodium/Iris; use one."),
    ({"phosphor"}, {"starlight"}, "Phosphor and Starlight both rewrite lighting; keep one."),
]
_cache: dict[tuple[str, int, float], dict[str, Any]] = {}
_FABRIC_MODULE = re.compile(r"fabric-api|fabric-api-base|fabric-[a-z0-9-]+-v\d+|fabric_[a-z0-9_]+_v\d+")


def _strip_comments(text: str) -> str:
    # Some mods ship fabric.mod.json with // comments or trailing commas.
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _fabric_meta(zf: zipfile.ZipFile, depth: int = 0) -> dict[str, Any] | None:
    try:
        raw = zf.read("fabric.mod.json").decode("utf-8", "replace")
    except KeyError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        try:
            data = json.loads(_strip_comments(raw))
        except ValueError:
            return {"id": "", "broken": True}
    if not isinstance(data, dict):
        return None
    provides = {str(p).lower() for p in data.get("provides") or [] if isinstance(p, str)}
    # Jar-in-jar: Fabric API and many libraries bundle their modules inside the jar.
    if depth < 2:
        for entry in data.get("jars") or []:
            name = entry.get("file") if isinstance(entry, dict) else None
            if not isinstance(name, str):
                continue
            try:
                inner = zipfile.ZipFile(io.BytesIO(zf.read(name)))
            except (KeyError, zipfile.BadZipFile, OSError, RuntimeError):
                continue
            meta = _fabric_meta(inner, depth + 1) or {}
            if meta.get("id"):
                provides.add(meta["id"])
            provides |= set(meta.get("provides") or [])
    deps = data.get("depends") if isinstance(data.get("depends"), dict) else {}
    breaks = data.get("breaks") if isinstance(data.get("breaks"), dict) else {}
    return {"id": str(data.get("id") or "").lower(), "name": str(data.get("name") or data.get("id") or ""),
            "version": str(data.get("version") or ""), "provides": sorted(provides),
            "depends": {str(k).lower(): v for k, v in deps.items()},
            "breaks": {str(k).lower(): v for k, v in breaks.items()}}


def read_jar(path: Path) -> dict[str, Any]:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime)
    if key in _cache:
        return _cache[key]
    info: dict[str, Any] = {"file": path.name, "kind": "unknown"}
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            meta = _fabric_meta(zf)
            if meta is not None:
                info.update(meta, kind="broken" if meta.get("broken") else "fabric")
            elif "quilt.mod.json" in names:
                info["kind"] = "quilt"
            elif "META-INF/mods.toml" in names or "META-INF/neoforge.mods.toml" in names or "mcmod.info" in names:
                info["kind"] = "forge"
                m = re.search(rb'modId\s*=\s*"([^"]+)"', zf.read("META-INF/mods.toml") if "META-INF/mods.toml" in names else b"")
                info["id"] = m.group(1).decode().lower() if m else ""
            elif any(n.startswith("net/optifine/") or n == "optifine/Installer.class" for n in names):
                info.update(kind="optifine", id="optifine", name="OptiFine")
    except (zipfile.BadZipFile, OSError, RuntimeError):
        info["kind"] = "broken"
    if len(_cache) > 4000:
        _cache.clear()
    _cache[key] = info
    return info


# ---- tiny version-range matcher for "depends": {"minecraft": ">=1.21 <1.22"} ----
def _parts(v: str) -> list[int] | None:
    v = v.strip().lstrip("v")
    v = re.split(r"[-+]", v, maxsplit=1)[0]
    if not re.fullmatch(r"\d+(\.(\d+|x|X|\*))*", v):
        return None
    return [int(p) if p.isdigit() else -1 for p in v.split(".")]


def _cmp(a: list[int], b: list[int]) -> int:
    n = max(len(a), len(b))
    a, b = a + [0] * (n - len(a)), b + [0] * (n - len(b))
    return (a > b) - (a < b)


def _term_ok(term: str, have: list[int]) -> bool | None:
    m = re.fullmatch(r"(>=|<=|>|<|=|~|\^)?\s*(.+)", term.strip())
    if not m:
        return None
    op, ver = m.group(1) or "", m.group(2)
    if ver in {"*", "x", "X"}:
        return True
    want = _parts(ver)
    if want is None:
        return None
    if -1 in want:  # 1.21.x
        cut = want.index(-1)
        return have[:cut] + [0] * max(0, cut - len(have)) == want[:cut]
    c = _cmp(have, want)
    if op == ">=":
        return c >= 0
    if op == "<=":
        return c <= 0
    if op == ">":
        return c > 0
    if op == "<":
        return c < 0
    if op == "~":  # same major.minor, >= given
        return c >= 0 and have[:2] == (want + [0, 0])[:2]
    if op == "^":
        return c >= 0 and have[:1] == want[:1]
    return c == 0 or (len(want) == 2 and have[:2] == want and len(have) <= 3 and op == "")


def version_matches(spec: Any, version: str) -> bool | None:
    """True/False when it can tell, None when the range is too unusual to judge (never warn then)."""
    have = _parts(version)
    if have is None:
        return None
    specs = spec if isinstance(spec, list) else [spec]
    seen_unknown = False
    for one in specs:
        if not isinstance(one, str):
            return None
        results = [_term_ok(t, have) for t in one.split()] or [True]
        if None in results:
            seen_unknown = True
            continue
        if all(results):
            return True
    return None if seen_unknown else False


def check(mods_dir: Path, game_version: str = "", loader: str = "fabric") -> dict[str, Any]:
    """Return {"ok", "warnings": [{"level": "error"|"warn", "title", "detail", "files"}], "count"}."""
    folder = Path(mods_dir)
    if not folder.is_dir():
        return {"ok": True, "warnings": [], "count": 0}
    jars = sorted(p for p in folder.glob("*.jar") if p.is_file())
    mods = [read_jar(p) for p in jars]
    warnings: list[dict[str, Any]] = []
    by_id: dict[str, list[dict[str, Any]]] = {}
    provided = set(BUILTIN)
    for m in mods:
        if m.get("id"):
            by_id.setdefault(m["id"], []).append(m)
            provided.add(m["id"])
        provided |= set(m.get("provides") or [])
    if "fabric-api" in provided or "fabric" in provided:
        provided |= {"fabric", "fabric-api"}
    loader = (loader or "fabric").lower()

    for mid, group in sorted(by_id.items()):
        if len(group) > 1:
            warnings.append({"level": "error", "title": f"{group[0].get('name') or mid} is installed {len(group)} times",
                             "detail": "Two copies of the same mod stop the game from starting. Keep the newest and remove the rest.",
                             "files": [g["file"] for g in group]})
    if loader in {"fabric", "quilt"}:
        forge = [m["file"] for m in mods if m["kind"] == "forge"]
        if forge:
            warnings.append({"level": "error", "title": "Forge mods in a Fabric instance",
                             "detail": "These jars are made for Forge/NeoForge and won't load on Fabric. Get the Fabric version or remove them.",
                             "files": forge})
    elif loader in {"forge", "neoforge"}:
        fab = [m["file"] for m in mods if m["kind"] == "fabric"]
        if fab:
            warnings.append({"level": "error", "title": "Fabric mods in a Forge instance",
                             "detail": "These jars are made for Fabric and won't load on Forge/NeoForge.", "files": fab})
    opti = [m["file"] for m in mods if m["kind"] == "optifine"]
    if opti and loader in {"fabric", "quilt"} and "optifabric" not in by_id:
        warnings.append({"level": "error", "title": "OptiFine jar in a Fabric instance",
                         "detail": "OptiFine needs OptiFabric on Fabric, and still clashes with Sodium. Most players use Sodium + Iris instead.",
                         "files": opti})
    broken = [m["file"] for m in mods if m["kind"] == "broken"]
    if broken:
        warnings.append({"level": "error", "title": "Damaged mod files",
                         "detail": "These jars can't be opened (half-downloaded or corrupt). Re-download or remove them.", "files": broken})
    ids = set(by_id) | set(opti and ["optifine"])
    for left, right, why in KNOWN_CLASHES:
        a, b = ids & left, ids & right
        if a and b:
            files = [g["file"] for i in sorted(a | b) for g in by_id.get(i, [])] or opti
            warnings.append({"level": "warn", "title": "Mods that don't work together", "detail": why, "files": files})
    if loader in {"fabric", "quilt"}:
        missing: dict[str, list[str]] = {}
        for m in mods:
            for dep in (m.get("depends") or {}):
                if dep in provided:
                    continue
                # Fabric API modules (fabric-xyz-v1 ...) all come from the one Fabric API jar.
                key = "fabric-api" if _FABRIC_MODULE.fullmatch(dep) else dep
                if key == "fabric-api" and "fabric-api" in provided:
                    continue
                missing.setdefault(key, []).append(m.get("name") or m["file"])
        for dep, who in sorted(missing.items()):
            who = sorted(set(who))
            nice = "Fabric API" if dep == "fabric-api" else dep
            warnings.append({"level": "error", "title": f"Missing {nice}",
                             "detail": f"Needed by {', '.join(who[:4])}{' and more' if len(who) > 4 else ''}. Install it from MODS.",
                             "files": [], "missing": dep})
        for m in mods:
            for dep, spec in (m.get("depends") or {}).items():
                if dep in BUILTIN or dep not in by_id:
                    continue
                target = by_id[dep][0]
                if version_matches(spec, target.get("version") or "") is False:
                    need = spec if isinstance(spec, str) else " or ".join(str(x) for x in spec)
                    warnings.append({"level": "warn", "title": f"{m.get('name') or m['file']} needs a different {target.get('name') or dep}",
                                     "detail": f"It asks for {target.get('name') or dep} {need}; you have {target.get('version') or 'unknown'}. Update one of them in MODS.",
                                     "files": [m["file"], target["file"]]})
            for other, spec in (m.get("breaks") or {}).items():
                if other in by_id:
                    target = by_id[other][0]
                    hit = version_matches(spec, target.get("version") or "")
                    if hit is not False:
                        warnings.append({"level": "warn", "title": f"{m.get('name') or m['file']} doesn't work with {target.get('name') or other}",
                                         "detail": "The mod itself says these two break each other.", "files": [m["file"], target["file"]]})
        if game_version:
            wrong = []
            for m in mods:
                spec = (m.get("depends") or {}).get("minecraft")
                if spec is not None and version_matches(spec, game_version) is False:
                    wrong.append(m["file"])
            if wrong:
                warnings.append({"level": "warn", "title": "Mods made for a different Minecraft version",
                                 "detail": f"These say they don't support Minecraft {game_version}. They may crash; update them in MODS.",
                                 "files": wrong})
    return {"ok": True, "warnings": warnings, "count": len(mods),
            "errors": sum(1 for w in warnings if w["level"] == "error")}
