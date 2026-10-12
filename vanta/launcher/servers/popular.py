"""Popular servers bar on the PLAY tab.

The list lives in ``<data>/popular-servers.json`` (seeded from DEFAULT_SERVERS
the first time), so it can be edited without touching code. Live status uses
the Minecraft server-list ping (handshake + status), with an SRV lookup over
DNS-over-HTTPS, a short timeout and a cache. Descriptions are the server's own
words (its MOTD or its own site/about text); the live MOTD is shown when the
ping succeeds.
"""

from __future__ import annotations

import json
import re
import socket
import struct
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

CONFIG_NAME = "popular-servers.json"
CACHE_SECONDS = 60
TIMEOUT = 4.0
PROTOCOL = 767  # any recent protocol works for a status ping

DEFAULT_SERVERS: list[dict[str, Any]] = [
    {"id": "pikanetwork", "name": "PikaNetwork", "host": "play.pika-network.net", "port": None,
     "description": "", "source": "live MOTD"},
    {"id": "fatalmc", "name": "FatalMC", "host": "mc.fatalmc.org", "port": None,
     "description": "A cracked network focused on Lifesteal and FFA (Java and Bedrock).",
     "source": "FatalMC's own server description"},
    {"id": "alwto", "name": "ALWTO", "host": "alwto.net", "port": None,
     "description": "ALWTO Practice: Duels, FFA, Party, Events.", "source": "server MOTD"},
    {"id": "bluemc", "name": "BlueMC", "host": "bluemc.org", "port": None,
     "description": "", "source": "live MOTD"},
    {"id": "rulexmc", "name": "RulexMC", "host": "rulexmc.mcsh.io", "port": None,
     "description": "", "source": "live MOTD"},
]

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


# ---- config -----------------------------------------------------------------

def load_servers(data_dir: Path) -> list[dict[str, Any]]:
    path = Path(data_dir) / CONFIG_NAME
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"servers": DEFAULT_SERVERS}, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [dict(item) for item in DEFAULT_SERVERS]
    rows = data.get("servers") if isinstance(data, dict) else data
    servers = []
    for item in rows or []:
        if not isinstance(item, dict) or not str(item.get("host") or "").strip():
            continue
        host = str(item["host"]).strip()
        servers.append({
            "id": str(item.get("id") or re.sub(r"[^a-z0-9]+", "-", host.lower())),
            "name": str(item.get("name") or host),
            "host": host,
            "port": _port(item.get("port")),
            "description": str(item.get("description") or ""),
            "source": str(item.get("source") or ""),
        })
    return servers


def _port(value: Any) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 0 < port < 65536 else None


def address(server: dict[str, Any]) -> str:
    return server["host"] + (f":{server['port']}" if server.get("port") and server["port"] != 25565 else "")


# ---- SRV via DNS-over-HTTPS --------------------------------------------------

def resolve_srv(host: str, fetch: Callable[[str], Any] | None = None) -> tuple[str, int] | None:
    url = "https://cloudflare-dns.com/dns-query?" + urllib.parse.urlencode({"name": f"_minecraft._tcp.{host}", "type": "SRV"})
    try:
        if fetch:
            data = fetch(url)
        else:
            request = urllib.request.Request(url, headers={"Accept": "application/dns-json"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - no SRV is normal
        return None
    for answer in data.get("Answer") or []:
        if answer.get("type") == 33:
            parts = str(answer.get("data") or "").split()
            if len(parts) == 4:
                return parts[3].rstrip("."), int(parts[2])
    return None


# ---- server list ping ---------------------------------------------------------

def _varint(value: int) -> bytes:
    out = b""
    value &= 0xFFFFFFFF
    while True:
        byte = value & 0x7F
        value >>= 7
        out += struct.pack("B", byte | (0x80 if value else 0))
        if not value:
            return out


def _read_varint(sock: socket.socket) -> int:
    result = 0
    for shift in range(0, 35, 7):
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("connection closed")
        byte = chunk[0]
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result
    raise ValueError("varint too long")


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("connection closed")
        data += chunk
    return data


def status_packets(host: str, port: int) -> bytes:
    host_bytes = host.encode("utf-8")
    handshake = b"\x00" + _varint(PROTOCOL) + _varint(len(host_bytes)) + host_bytes + struct.pack(">H", port) + _varint(1)
    return _varint(len(handshake)) + handshake + b"\x01\x00"


def ping(host: str, port: int, timeout: float = TIMEOUT) -> dict[str, Any]:
    started = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(status_packets(host, port))
        _read_varint(sock)  # packet length
        if _read_varint(sock) != 0:
            raise ValueError("unexpected packet")
        length = _read_varint(sock)
        payload = json.loads(_recv_exact(sock, length).decode("utf-8", errors="replace"))
    latency = int((time.monotonic() - started) * 1000)
    return parse_status(payload) | {"latencyMs": latency}


def motd_text(component: Any) -> str:
    if isinstance(component, str):
        text = component
    elif isinstance(component, dict):
        text = str(component.get("text") or "") + "".join(motd_text(part) for part in component.get("extra") or [])
    elif isinstance(component, list):
        text = "".join(motd_text(part) for part in component)
    else:
        text = ""
    return re.sub(r"§.", "", text)


def parse_status(payload: dict[str, Any]) -> dict[str, Any]:
    players = payload.get("players") or {}
    motd = re.sub(r"[ \t]+", " ", motd_text(payload.get("description"))).strip()
    return {
        "online": True,
        "players": int(players.get("online") or 0),
        "maxPlayers": int(players.get("max") or 0),
        "version": re.sub(r"§.", "", str((payload.get("version") or {}).get("name") or "")),
        "motd": "\n".join(line.strip() for line in motd.splitlines() if line.strip()),
    }


def server_status(server: dict[str, Any], *, pinger: Callable[..., dict[str, Any]] = ping, srv: Callable[[str], Any] = resolve_srv) -> dict[str, Any]:
    key = f"{server['host']}:{server.get('port')}"
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return dict(hit[1], cached=True)
    targets: list[tuple[str, int]] = []
    if server.get("port"):
        targets.append((server["host"], int(server["port"])))
    else:
        found = srv(server["host"])
        if found:
            targets.append(found)
        targets.append((server["host"], 25565))
    result: dict[str, Any] = {"online": False, "error": "No reply"}
    for target_host, target_port in targets:
        try:
            result = pinger(target_host, target_port) | {"resolved": f"{target_host}:{target_port}"}
            break
        except Exception as exc:  # noqa: BLE001
            result = {"online": False, "error": f"{type(exc).__name__}: {exc}"[:160]}
    result["checkedAt"] = int(now)
    with _cache_lock:
        _cache[key] = (now, result)
    return dict(result, cached=False)


def statuses(servers: list[dict[str, Any]], **kwargs: Any) -> list[dict[str, Any]]:
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(servers)))) as pool:
        found = list(pool.map(lambda s: server_status(s, **kwargs), servers))
    return [dict(server, address=address(server), status=status) for server, status in zip(servers, found)]


# ---- servers.dat (uncompressed NBT) ----------------------------------------------

TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG, TAG_FLOAT, TAG_DOUBLE, TAG_BYTE_ARRAY, TAG_STRING, TAG_LIST, TAG_COMPOUND, TAG_INT_ARRAY, TAG_LONG_ARRAY = range(13)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def take(self, n: int) -> bytes:
        chunk = self.data[self.pos:self.pos + n]
        if len(chunk) != n:
            raise ValueError("truncated NBT")
        self.pos += n
        return chunk

    def string(self) -> str:
        (length,) = struct.unpack(">H", self.take(2))
        return self.take(length).decode("utf-8", errors="replace")

    def payload(self, tag: int) -> Any:
        fixed = {TAG_BYTE: ">b", TAG_SHORT: ">h", TAG_INT: ">i", TAG_LONG: ">q", TAG_FLOAT: ">f", TAG_DOUBLE: ">d"}
        if tag in fixed:
            fmt = fixed[tag]
            return (tag, struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0])
        if tag == TAG_BYTE_ARRAY:
            (n,) = struct.unpack(">i", self.take(4))
            return (tag, self.take(n))
        if tag == TAG_STRING:
            return (tag, self.string())
        if tag == TAG_LIST:
            inner = self.take(1)[0]
            (n,) = struct.unpack(">i", self.take(4))
            return (tag, (inner, [self.payload(inner)[1] for _ in range(n)]))
        if tag == TAG_COMPOUND:
            items = []
            while True:
                child = self.take(1)[0]
                if child == TAG_END:
                    return (tag, items)
                name = self.string()
                items.append((name, self.payload(child)))
        if tag in (TAG_INT_ARRAY, TAG_LONG_ARRAY):
            (n,) = struct.unpack(">i", self.take(4))
            size = 4 if tag == TAG_INT_ARRAY else 8
            return (tag, self.take(n * size))
        raise ValueError(f"bad NBT tag {tag}")


def _write_payload(tag: int, value: Any) -> bytes:
    fixed = {TAG_BYTE: ">b", TAG_SHORT: ">h", TAG_INT: ">i", TAG_LONG: ">q", TAG_FLOAT: ">f", TAG_DOUBLE: ">d"}
    if tag in fixed:
        return struct.pack(fixed[tag], value)
    if tag == TAG_BYTE_ARRAY:
        return struct.pack(">i", len(value)) + value
    if tag == TAG_STRING:
        raw = value.encode("utf-8")
        return struct.pack(">H", len(raw)) + raw
    if tag == TAG_LIST:
        inner, items = value
        return bytes([inner]) + struct.pack(">i", len(items)) + b"".join(_write_payload(inner, item) for item in items)
    if tag == TAG_COMPOUND:
        out = b""
        for name, (child, child_value) in value:
            raw = name.encode("utf-8")
            out += bytes([child]) + struct.pack(">H", len(raw)) + raw + _write_payload(child, child_value)
        return out + bytes([TAG_END])
    if tag in (TAG_INT_ARRAY, TAG_LONG_ARRAY):
        size = 4 if tag == TAG_INT_ARRAY else 8
        return struct.pack(">i", len(value) // size) + value
    raise ValueError(f"bad NBT tag {tag}")


def read_server_list(path: Path) -> list[dict[str, str]]:
    root = _read_root(path)
    return [_entry_dict(entry) for entry in _servers_list(root)[1]]


def _read_root(path: Path) -> list[Any]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    if not data:
        return []
    reader = _Reader(data)
    tag = reader.take(1)[0]
    if tag != TAG_COMPOUND:
        raise ValueError("servers.dat root is not a compound")
    reader.string()
    return reader.payload(TAG_COMPOUND)[1]


def _servers_list(root: list[Any]) -> tuple[int, list[Any]]:
    for name, (tag, value) in root:
        if name == "servers" and tag == TAG_LIST:
            return value
    return (TAG_COMPOUND, [])


def _entry_dict(entry: list[Any]) -> dict[str, str]:
    return {name: value for name, (tag, value) in entry if tag == TAG_STRING}


def add_to_server_list(path: Path, name: str, ip: str) -> bool:
    """Append {name, ip} to servers.dat (keeps every existing entry). False if already there."""
    path = Path(path)
    root = _read_root(path)
    inner, entries = _servers_list(root)
    if any(_entry_dict(entry).get("ip", "").lower() == ip.lower() for entry in entries):
        return False
    entries = list(entries) + [[("name", (TAG_STRING, name)), ("ip", (TAG_STRING, ip))]]
    root = [item for item in root if item[0] != "servers"] + [("servers", (TAG_LIST, (TAG_COMPOUND, entries)))]
    raw = bytes([TAG_COMPOUND]) + struct.pack(">H", 0) + _write_payload(TAG_COMPOUND, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        path.with_suffix(".dat_old").write_bytes(path.read_bytes())
    tmp = path.with_suffix(".dat.tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)
    return True


def join_arguments(version_number: str, host: str, port: int | None) -> list[str]:
    """--quickPlayMultiplayer on 1.20+, --server/--port before that."""
    parts = []
    for piece in version_number.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            break
    target = host + (f":{port}" if port else "")
    if tuple(parts[:2]) >= (1, 20) or (parts and parts[0] >= 26):
        return ["--quickPlayMultiplayer", target]
    return ["--server", host, "--port", str(port or 25565)]
