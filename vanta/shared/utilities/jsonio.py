"""Atomic JSON reads and writes."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

# One lock per file. On Windows a replace fails with "Access is denied" while
# another thread has the file open, so readers and writers in this process
# take turns, and a replace blocked by something outside (antivirus) is retried.
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(str(path)))
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    with _lock_for(path):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Concurrent writers can leave trailing junk after a valid object.
        try:
            data, end = json.JSONDecoder().raw_decode(text.lstrip())
        except json.JSONDecodeError:
            return default
        leftover = text.lstrip()[end:].strip()
        if leftover:
            try:
                write_json(path, data)
            except OSError:
                pass
        return data


def write_json(path: Path, data: Any, *, attempts: int = 12) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    with _lock_for(path):
        try:
            if path.is_file() and path.read_text(encoding="utf-8") == payload:
                return  # unchanged: no write, nothing to collide with
        except OSError:
            pass
        temporary = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(payload, encoding="utf-8")
        for attempt in range(attempts):
            try:
                temporary.replace(path)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    try:
                        temporary.unlink()
                    except OSError:
                        pass
                    raise
                time.sleep(0.05 * (attempt + 1))
