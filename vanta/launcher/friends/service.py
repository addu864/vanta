"""Vanta Friends without a server: local friend list, LAN presence, e4all world invites."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

from vanta.launcher.friends import lan as lanmod
from vanta.launcher.friends.codes import CodeError, friend_link, make_invite, parse_friend_code, parse_invite
from vanta.launcher.friends.store import FriendStore

E4MC_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+e4mc\.link)\b", re.I)
LAN_PORT_RE = re.compile(r"(?:Started serving on|hosted on port|Local game hosted on port)\D{0,20}(\d{2,5})", re.I)
STOP_MARK = "Stopping singleplayer server"


def _log_tail(instance: Path) -> str:
    try:
        return (Path(instance) / "logs" / "latest.log").read_text(encoding="utf-8", errors="replace")[-400_000:]
    except OSError:
        return ""


def _after(text: str, needle: str) -> str:
    return text.rsplit(needle, 1)[-1]


def find_shared_address(instance: Path) -> str | None:
    """Newest e4all/e4mc public address in latest.log, unless the world was closed after it."""
    text = _log_tail(instance)
    found = E4MC_RE.findall(text)
    if not found or STOP_MARK in _after(text, found[-1]):
        return None
    return found[-1].lower()


def find_lan_port(instance: Path) -> int | None:
    """Port of the world opened with Open to LAN, if it is still open."""
    text = _log_tail(instance)
    hits = list(LAN_PORT_RE.finditer(text))
    if not hits or STOP_MARK in text[hits[-1].end():]:
        return None
    port = int(hits[-1].group(1))
    return port if 0 < port < 65536 else None


def newest_world(instance: Path) -> str:
    try:
        saves = [p for p in (Path(instance) / "saves").iterdir() if p.is_dir()]
    except OSError:
        return ""
    return max(saves, key=lambda p: p.stat().st_mtime).name[:64] if saves else ""


def firewall_hint() -> str:
    if sys.platform.startswith("win"):
        return ("If friends on the same Wi-Fi don't show up, allow Vanta on Private networks when Windows asks, "
                "or in Windows Security → Firewall → Allow an app. Use the Private network profile at home.")
    if sys.platform == "darwin":
        return "If friends on the same Wi-Fi don't show up, allow incoming connections for Vanta in System Settings → Network → Firewall."
    return "If friends on the same network don't show up, allow UDP port 47786 in your firewall."


class FriendsService:
    def __init__(self, data_dir: Path, *, lan: lanmod.LanPresence | None = None,
                 name: Callable[[], str] = lambda: "Player", clock: Callable[[], float] = time.time) -> None:
        self.store = FriendStore(Path(data_dir))
        self._name = name
        self._clock = clock
        self._presence: dict[str, Any] = {"status": "online", "mc": "", "world": "", "port": None}
        self.lan = lan if lan is not None else lanmod.LanPresence(self._beacon)
        self._lan_started = lan is not None
        self._started_at = time.monotonic()

    # ---- identity / beacon ----
    def me(self) -> dict[str, str]:
        ident = self.store.identity(self._name())
        return {**ident, "link": friend_link(ident["code"])}

    def _beacon(self) -> dict[str, Any]:
        me = self.store.identity(self._name())
        return {"id": me["id"], "name": me["name"], "code": me["code"], **self._presence,
                "knows": sorted(self.store.ids())[:64]}

    def ensure_lan(self) -> None:
        if not self._lan_started:
            self._lan_started = True
            self.lan.start()

    def set_presence(self, *, playing: bool, mc: str = "", world: str = "", port: int | None = None) -> None:
        self._presence = {"status": "playing" if playing else "online", "mc": mc if playing else "",
                          "world": world if playing and port else "", "port": port if playing else None}

    # ---- friends ----
    def add(self, text: str, source: str = "code") -> dict[str, Any]:
        return self.store.add(text, my_id=self.me()["id"], source=source)

    def remove(self, ident: str) -> dict[str, Any]:
        if not self.store.remove(str(ident or "").upper()):
            raise CodeError("That friend isn't in your list.")
        return {"ok": True}

    def overview(self) -> dict[str, Any]:
        me = self.me()
        peers = self.lan.online()
        friends = []
        for f in self.store.friends():
            p = peers.get(f["id"])
            row = {**f, "lan": bool(p), "online": bool(p), "mutual": bool(p and me["id"] in p["knows"])}
            if p:
                row.update(status=p["status"], mc=p["mc"], world=p["world"], name=p["name"] or f["name"])
                row["join"] = f"{p['ip']}:{p['port']}" if p.get("port") else None
            friends.append(row)
        friends.sort(key=lambda r: (not r["online"], not r.get("join"), r["name"].lower()))
        known = self.store.ids()
        nearby = [{"id": p["id"], "name": p["name"], "code": p["code"], "status": p["status"], "mc": p["mc"],
                   "addedYou": me["id"] in p["knows"]} for p in peers.values() if p["id"] not in known and p.get("code")]
        nearby.sort(key=lambda r: (not r["addedYou"], r["name"].lower()))
        lan = {"ok": not self.lan.error, "error": self.lan.error, "sent": self.lan.sent, "received": self.lan.received,
               "hint": firewall_hint()}
        lan["quiet"] = lan["ok"] and self.lan.received == 0 and time.monotonic() - self._started_at > 20
        return {"ok": True, "me": me, "friends": friends, "nearby": nearby, "lan": lan, "presence": dict(self._presence)}

    def lan_join_target(self, ident: str) -> dict[str, Any]:
        f = next((x for x in self.overview()["friends"] if x["id"] == str(ident or "").upper()), None)
        if not f:
            raise CodeError("That friend isn't in your list.")
        if not f.get("join"):
            raise CodeError(f"{f['name']} isn't hosting a LAN world right now." if f["online"]
                            else f"{f['name']} isn't on your network right now.")
        host, _, port = f["join"].partition(":")
        return {"host": host, "port": int(port), "version": f.get("mc") or "", "name": f["name"]}

    # ---- remote invites (e4all) ----
    def invite_for(self, instance: Path, mc_version: str, loader: str = "fabric", address: str = "") -> dict[str, Any]:
        addr = (address or "").strip() or find_shared_address(instance)
        if not addr:
            raise CodeError("No e4all address yet. In Minecraft press Esc → Open to LAN (Online Mode off) → Start, then try again.")
        code = make_invite(addr, mc_version, self.me()["name"], loader=loader, now=self._clock())
        return {"ok": True, "invite": code, "address": addr, "link": "vanta://join/" + code}

    def read_invite(self, text: str) -> dict[str, Any]:
        return parse_invite(text, now=self._clock())
