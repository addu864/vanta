"""Per-instance playtime, counted from Vanta's own game sessions (local file, never uploaded)."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from vanta.shared.utilities.jsonio import read_json, write_json

FILE = "playtime.json"
MAX_SESSION = 24 * 3600  # one session never counts for more than a day
_lock = threading.Lock()


def _key(instance: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(instance)))


def _load(data_dir: Path) -> dict[str, Any]:
    data = read_json(Path(data_dir) / FILE, {})
    return data if isinstance(data, dict) and isinstance(data.get("instances"), dict) else {"instances": {}}


def session_end(instance: Path, started: float, seen: float, now: float | None = None) -> float:
    """Best guess for when the game closed: the later of the last time Vanta saw it running and
    the last write to the game's latest.log, never after now."""
    now = time.time() if now is None else now
    end = seen or started
    try:
        log = Path(instance) / "logs" / "latest.log"
        if log.is_file():
            end = max(end, log.stat().st_mtime)
    except OSError:
        pass
    return min(end, now)


def record(data_dir: Path, instance: Path, started: float, ended: float) -> int:
    seconds = int(max(0.0, min(float(ended) - float(started), MAX_SESSION)))
    if seconds <= 0:
        return 0
    key = _key(instance)
    with _lock:
        data = _load(data_dir)
        row = data["instances"].get(key) if isinstance(data["instances"].get(key), dict) else {}
        last_start = float(row.get("lastStart") or 0)
        if abs(last_start - float(started)) < 1:  # same session judged twice (e.g. two polls at once)
            return 0
        recent = [r for r in (row.get("recent") or []) if isinstance(r, list) and len(r) == 2][-59:]
        recent.append([int(ended), seconds])
        row.update(seconds=int(row.get("seconds") or 0) + seconds, sessions=int(row.get("sessions") or 0) + 1,
                   last=int(ended), lastStart=float(started), lastSession=seconds, recent=recent)
        data["instances"][key] = row
        write_json(Path(data_dir) / FILE, data)
    return seconds


def reset(data_dir: Path, instance: Path) -> int:
    """Forget one instance's playtime. Returns the seconds that were cleared."""
    key = _key(instance)
    with _lock:
        data = _load(data_dir)
        row = data["instances"].pop(key, None)
        if not isinstance(row, dict):
            return 0
        write_json(Path(data_dir) / FILE, data)
    return int(row.get("seconds") or 0)


def for_instance(data_dir: Path, instance: Path) -> dict[str, Any]:
    row = _load(data_dir)["instances"].get(_key(instance)) or {}
    week = sum(int(i[1]) for i in row.get("recent") or [] if isinstance(i, list) and len(i) == 2 and float(i[0]) >= time.time() - 7 * 86400)
    return {"seconds": int(row.get("seconds") or 0), "sessions": int(row.get("sessions") or 0),
            "last": int(row.get("last") or 0), "lastSession": int(row.get("lastSession") or 0), "week": week}


def total(data_dir: Path) -> int:
    return sum(int((r or {}).get("seconds") or 0) for r in _load(data_dir)["instances"].values() if isinstance(r, dict))


def recent_total(data_dir: Path, days: float = 7, now: float | None = None, instance: Path | None = None) -> int:
    """Seconds played in the last `days` days (from the last 60 sessions of each instance)."""
    cutoff = (time.time() if now is None else now) - days * 86400
    rows = _load(data_dir)["instances"]
    if instance is not None:
        rows = {k: v for k, v in rows.items() if k == _key(instance)}
    out = 0
    for row in rows.values():
        for item in (row or {}).get("recent") or []:
            if isinstance(item, list) and len(item) == 2 and float(item[0]) >= cutoff:
                out += int(item[1])
    return out


def nice(seconds: int) -> str:
    seconds = int(seconds or 0)
    if seconds < 60:
        return "under a minute" if seconds else "0m"
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"
