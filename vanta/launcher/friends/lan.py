"""LAN presence: a small JSON beacon over UDP multicast + broadcast every few seconds.

Nothing leaves the local network. Packets are capped, unknown fields ignored, and a peer only
counts as online for TIMEOUT_S after its last beacon.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Any, Callable

PORT = 47786
GROUP = "239.255.86.77"
INTERVAL_S = 4.0
TIMEOUT_S = 15.0
MAX_PACKET = 1400
MAGIC = "vanta-lan"


def encode(beacon: dict[str, Any]) -> bytes:
    body = {"t": MAGIC, "p": 1, **{k: v for k, v in beacon.items() if v not in (None, "")}}
    raw = json.dumps(body, separators=(",", ":")).encode()
    if len(raw) > MAX_PACKET:  # trim the friend-id list first
        body["knows"] = body.get("knows", [])[:20]
        raw = json.dumps(body, separators=(",", ":")).encode()
    return raw[:MAX_PACKET]


def decode(raw: bytes) -> dict[str, Any] | None:
    if len(raw) > MAX_PACKET:
        return None
    try:
        msg = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(msg, dict) or msg.get("t") != MAGIC or not isinstance(msg.get("id"), str):
        return None
    out: dict[str, Any] = {"id": msg["id"][:10].upper(), "name": str(msg.get("name") or "")[:16],
                           "code": str(msg.get("code") or "")[:40], "status": str(msg.get("status") or "online")[:16],
                           "mc": str(msg.get("mc") or "")[:32], "world": str(msg.get("world") or "")[:64]}
    port = msg.get("port")
    out["port"] = port if isinstance(port, int) and 0 < port < 65536 else None
    knows = msg.get("knows")
    out["knows"] = [str(k)[:10].upper() for k in knows[:64]] if isinstance(knows, list) else []
    return out


class LanPresence:
    """Sends our beacon and collects peers. targets defaults to multicast + broadcast; tests pass loopback."""

    def __init__(self, beacon: Callable[[], dict[str, Any]], *, port: int = PORT,
                 targets: list[tuple[str, int]] | None = None, clock: Callable[[], float] = time.monotonic,
                 interval_s: float = INTERVAL_S, bind_host: str = "") -> None:
        self.beacon = beacon
        self.port = port
        self.targets = targets if targets is not None else [(GROUP, port), ("255.255.255.255", port)]
        self.clock = clock
        self.interval_s = interval_s
        self.bind_host = bind_host
        self.peers: dict[str, dict[str, Any]] = {}
        self.error = ""
        self.sent = 0
        self.received = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []

    # ---- sockets ----
    def _open(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind((self.bind_host, self.port))
        if any(t[0] == GROUP for t in self.targets):
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                             struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton("0.0.0.0")))
            except OSError:
                pass  # no multicast route; broadcast still works
        s.settimeout(1.0)
        return s

    def start(self) -> bool:
        if self._sock is not None:
            return True
        try:
            self._sock = self._open()
        except OSError as exc:
            self.error = (f"LAN discovery is off: port {self.port} is busy or blocked ({exc.strerror or exc}). "
                          "Friend codes and world invites still work.")
            return False
        self.error = ""
        self._stop.clear()
        for fn in (self._send_loop, self._recv_loop):
            t = threading.Thread(target=fn, name=f"vanta-lan-{fn.__name__}", daemon=True)
            t.start()
            self._threads.append(t)
        return True

    def stop(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=2)
        self._threads.clear()

    def send_once(self) -> None:
        sock = self._sock
        if sock is None:
            return
        raw = encode(self.beacon())
        ok = False
        for target in self.targets:
            try:
                sock.sendto(raw, target)
                ok = True
            except OSError:
                continue
        if ok:
            self.sent += 1
        elif not self.error:
            self.error = "Vanta couldn't send on your network (no Wi-Fi/Ethernet, or blocked)."

    def _send_loop(self) -> None:
        while not self._stop.is_set():
            self.send_once()
            self._stop.wait(self.interval_s)

    def _recv_loop(self) -> None:
        my_id = str(self.beacon().get("id") or "")
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                return
            try:
                raw, (ip, _) = sock.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.5)
                continue
            self.handle(raw, ip, my_id)

    def handle(self, raw: bytes, ip: str, my_id: str = "") -> dict[str, Any] | None:
        msg = decode(raw)
        if msg is None or msg["id"] == my_id:
            return None
        msg["ip"] = ip
        msg["seen"] = self.clock()
        with self._lock:
            self.peers[msg["id"]] = msg
            self.received += 1
        return msg

    def online(self) -> dict[str, dict[str, Any]]:
        now = self.clock()
        with self._lock:
            for k in [k for k, v in self.peers.items() if now - v["seen"] > TIMEOUT_S]:
                del self.peers[k]
            return {k: dict(v) for k, v in self.peers.items()}
