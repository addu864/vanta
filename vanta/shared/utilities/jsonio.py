"""Atomic JSON reads and writes."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    text = path.read_text(encoding="utf-8")
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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(path)
