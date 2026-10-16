"""Local identity and friend list, stored in <data>/friends/friends.json."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from vanta.launcher.friends.codes import CodeError, friend_code, new_id, parse_friend_code

MAX_FRIENDS = 200


class FriendStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "friends" / "friends.json"
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("friends", [])
                return data
        except (OSError, ValueError):
            pass
        return {"friends": []}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def identity(self, name: str) -> dict[str, str]:
        """The install's id is random and permanent; the name follows the selected local account."""
        with self._lock:
            data = self._read()
            if not data.get("id"):
                data["id"] = new_id()
                self._write(data)
            ident = str(data["id"])
        try:
            code = friend_code(name, ident)
        except CodeError:
            name = "Player"
            code = friend_code(name, ident)
        return {"name": name, "id": ident, "code": code}

    def friends(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._read()["friends"])

    def add(self, text: str, *, my_id: str = "", source: str = "code") -> dict[str, Any]:
        parsed = parse_friend_code(text)
        if parsed["id"] == my_id:
            raise CodeError("That's your own friend code.")
        with self._lock:
            data = self._read()
            for f in data["friends"]:
                if f["id"] == parsed["id"]:
                    f["name"] = parsed["name"]
                    f["code"] = parsed["code"]
                    self._write(data)
                    return {"ok": True, "friend": f, "already": True}
            if len(data["friends"]) >= MAX_FRIENDS:
                raise CodeError(f"You already have {MAX_FRIENDS} friends.")
            f = {"id": parsed["id"], "name": parsed["name"], "code": parsed["code"], "added": int(time.time()), "via": source}
            data["friends"].append(f)
            self._write(data)
            return {"ok": True, "friend": f, "already": False}

    def remove(self, ident: str) -> bool:
        with self._lock:
            data = self._read()
            keep = [f for f in data["friends"] if f["id"] != ident]
            changed = len(keep) != len(data["friends"])
            data["friends"] = keep
            if changed:
                self._write(data)
            return changed

    def ids(self) -> set[str]:
        return {f["id"] for f in self.friends()}
