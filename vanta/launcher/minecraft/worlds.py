"""World manager: list, back up, restore and delete singleplayer worlds of one instance.

Backups are plain zip files in <data>/backups/<instance>/. Delete always makes a backup
first and needs the world's folder name typed back as confirmation. Restore never
overwrites: it unpacks into a new folder when the name is taken.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import struct
import time
import zipfile
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00]{1,80}$")
BACKUP_RE = re.compile(r"^[^/\\]{1,120}\.zip$")


class WorldError(ValueError):
    pass


def saves_dir(instance: Path) -> Path:
    return Path(instance) / "saves"


def _check_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name) or name in {".", ".."} or name.startswith("."):
        raise WorldError("That isn't a valid world folder name.")
    return name


def world_path(instance: Path, name: str) -> Path:
    path = saves_dir(instance) / _check_name(name)
    if not (path / "level.dat").is_file():
        raise WorldError(f"There is no world called {name} in this instance.")
    return path


def level_name(level_dat: Path) -> str:
    """LevelName from level.dat (gzip NBT). Falls back to '' if it can't be read."""
    try:
        raw = gzip.decompress(level_dat.read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile):
        return ""
    key = b"\x08\x00\x09LevelName"  # TAG_String, name length 9, "LevelName"
    i = raw.find(key)
    if i < 0:
        return ""
    j = i + len(key)
    try:
        (length,) = struct.unpack(">H", raw[j:j + 2])
        return raw[j + 2:j + 2 + length].decode("utf-8", "replace")[:80]
    except struct.error:
        return ""


def _size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def list_worlds(instance: Path) -> list[dict[str, Any]]:
    out = []
    base = saves_dir(instance)
    if not base.is_dir():
        return out
    for folder in base.iterdir():
        level = folder / "level.dat"
        if not folder.is_dir() or not level.is_file():
            continue
        out.append({"folder": folder.name, "name": level_name(level) or folder.name, "sizeMb": round(_size(folder) / 1048576, 1),
                    "lastPlayed": int(level.stat().st_mtime), "icon": (folder / "icon.png").is_file()})
    out.sort(key=lambda w: w["lastPlayed"], reverse=True)
    return out


def backup(instance: Path, name: str, backups: Path, *, label: str = "") -> dict[str, Any]:
    src = world_path(instance, name)
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    safe = re.sub(r"[^\w .()-]", "_", src.name)[:60]
    target = backups / f"{safe}__{stamp}{('__' + label) if label else ''}.zip"
    n = 1
    while target.exists():
        target = target.with_name(f"{target.stem}_{n}.zip")
        n += 1
    tmp = target.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(src):
            for f in files:
                if f == "session.lock":
                    continue
                full = Path(root) / f
                z.write(full, Path(src.name) / full.relative_to(src))
    tmp.replace(target)
    return {"ok": True, "file": target.name, "sizeMb": round(target.stat().st_size / 1048576, 1)}


def list_backups(backups: Path) -> list[dict[str, Any]]:
    if not backups.is_dir():
        return []
    rows = []
    for z in backups.glob("*.zip"):
        st = z.stat()
        rows.append({"file": z.name, "world": z.name.split("__")[0], "at": int(st.st_mtime),
                     "sizeMb": round(st.st_size / 1048576, 1), "auto": "__before-delete" in z.name or "__before-restore" in z.name})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows


def _backup_file(backups: Path, file: str) -> Path:
    if not BACKUP_RE.match(file or "") or file.startswith("."):
        raise WorldError("Unknown backup.")
    path = backups / file
    if not path.is_file():
        raise WorldError("That backup no longer exists.")
    return path


def restore(instance: Path, backups: Path, file: str) -> dict[str, Any]:
    archive = _backup_file(backups, file)
    base = saves_dir(instance)
    base.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        tops = {n.replace("\\", "/").split("/")[0] for n in names}
        if len(tops) != 1:
            raise WorldError("That zip doesn't contain exactly one world.")
        top = _check_name(tops.pop())
        if not any(n.replace("\\", "/") == f"{top}/level.dat" for n in names):
            raise WorldError("That zip has no level.dat, so it isn't a Minecraft world.")
        dest_name = top
        if (base / dest_name).exists():
            dest_name = f"{top} (restored {time.strftime('%Y-%m-%d %H%M')})"
            n = 2
            while (base / dest_name).exists():
                dest_name = f"{top} (restored {time.strftime('%Y-%m-%d %H%M')} {n})"
                n += 1
        dest = (base / dest_name).resolve()
        tmp = base / f".restoring-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        try:
            root = tmp.resolve()
            for info in z.infolist():
                rel = info.filename.replace("\\", "/")
                if info.is_dir():
                    continue
                out = (root / rel).resolve()
                if not out.is_relative_to(root):
                    raise WorldError("Unsafe path in backup.")
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, out.open("wb") as fh:
                    shutil.copyfileobj(src, fh)
            (tmp / top).rename(dest)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"ok": True, "folder": dest_name, "renamed": dest_name != top}


def delete(instance: Path, name: str, confirm: str, backups: Path) -> dict[str, Any]:
    path = world_path(instance, name)
    if (confirm or "").strip() != path.name:
        raise WorldError(f"Type the world folder name ({path.name}) to confirm.")
    safety = backup(instance, path.name, backups, label="before-delete")
    shutil.rmtree(path)
    return {"ok": True, "backup": safety["file"]}


def delete_backup(backups: Path, file: str) -> dict[str, Any]:
    _backup_file(backups, file).unlink()
    return {"ok": True}
