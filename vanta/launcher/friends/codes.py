"""Friend codes and world invite codes. Pure functions, no network."""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import time
import zlib
from typing import Any

B32 = "ABCDEFGHJKMNPQRSTVWXYZ0123456789"  # Crockford-ish: no I, L, O, U
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z.\-_ ]{1,32}$")
INVITE_PREFIX = "VW1."
INVITE_TTL_S = 6 * 3600


class CodeError(ValueError):
    pass


def new_id() -> str:
    return "".join(secrets.choice(B32) for _ in range(10))


def _check(text: str) -> str:
    n = zlib.crc32(text.upper().encode()) & 0x3FF
    return B32[n >> 5] + B32[n & 31]


def friend_code(name: str, ident: str) -> str:
    """itz_alone10-7KQ2M9XAF3-QW: name, 10-char random id, 2-char checksum."""
    if not NAME_RE.match(name or ""):
        raise CodeError("Your Vanta username must be 1-16 letters, digits or _.")
    if len(ident) != 10 or any(c not in B32 for c in ident):
        raise CodeError("Bad friend id.")
    return f"{name}-{ident}-{_check(name + ident)}"


def _strip_link(text: str, kind: str) -> str:
    t = (text or "").strip().strip("<>\"'")
    low = t.lower()
    for pre in (f"vanta://{kind}/", f"vanta:{kind}/", f"vanta://{kind}?code="):
        if low.startswith(pre):
            return t[len(pre):].strip("/")
    return t


def parse_friend_code(text: str) -> dict[str, str]:
    code = _strip_link(text, "friend")
    m = re.match(r"^([A-Za-z0-9_]{1,16})-([A-Za-z0-9]{10})-([A-Za-z0-9]{2})$", code)
    if not m:
        raise CodeError("That isn't a Vanta friend code. It looks like name-XXXXXXXXXX-XX.")
    name, ident, chk = m.group(1), m.group(2).upper(), m.group(3).upper()
    ident = ident.replace("O", "0").replace("I", "1").replace("L", "1")
    if any(c not in B32 for c in ident) or _check(name + ident) != chk:
        raise CodeError("That friend code has a typo (the check letters don't match).")
    return {"name": name, "id": ident, "code": f"{name}-{ident}-{chk}"}


def friend_link(code: str) -> str:
    return f"vanta://friend/{code}"


def _addr(address: str) -> tuple[str, int | None]:
    a = (address or "").strip().lower()
    host, _, port = a.partition(":")
    if not HOST_RE.match(host) or ".." in host or host.startswith((".", "-")):
        raise CodeError("That world address doesn't look right.")
    p = None
    if port:
        if not port.isdigit() or not 0 < int(port) < 65536:
            raise CodeError("That world address has a bad port.")
        p = int(port)
    return host, p


def make_invite(address: str, mc_version: str, host_name: str, *, loader: str = "fabric",
                ttl_s: int = INVITE_TTL_S, now: float | None = None) -> str:
    host, port = _addr(address)
    if not VERSION_RE.match(mc_version or ""):
        raise CodeError("Unknown Minecraft version for the invite.")
    data = {"a": host + (f":{port}" if port else ""), "v": mc_version, "h": (host_name or "")[:16],
            "l": loader or "fabric", "x": int((now if now is not None else time.time()) + ttl_s)}
    raw = json.dumps(data, separators=(",", ":")).encode()
    return INVITE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_invite(text: str, *, now: float | None = None) -> dict[str, Any]:
    code = _strip_link(text, "join").replace("\n", "").replace(" ", "")
    if not code.startswith(INVITE_PREFIX):
        raise CodeError("That isn't a Vanta world invite. It starts with VW1.")
    body = code[len(INVITE_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise CodeError("That world invite is damaged. Ask for a fresh one.") from None
    if not isinstance(data, dict):
        raise CodeError("That world invite is damaged. Ask for a fresh one.")
    host, port = _addr(str(data.get("a") or ""))
    version = str(data.get("v") or "")
    if not VERSION_RE.match(version):
        raise CodeError("That world invite has no Minecraft version.")
    name = str(data.get("h") or "")
    if name and not NAME_RE.match(name):
        name = re.sub(r"[^A-Za-z0-9_]", "", name)[:16]
    expires = int(data.get("x") or 0)
    if expires and expires < (now if now is not None else time.time()):
        raise CodeError(f"This invite from {name or 'your friend'} has expired. Ask for a new one.")
    loader = str(data.get("l") or "fabric").lower()
    return {"host": host, "port": port, "address": host + (f":{port}" if port else ""), "version": version,
            "hostName": name, "loader": loader if loader in {"fabric", "vanilla"} else "fabric", "expires": expires}
