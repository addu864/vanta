"""Reject token-shaped payloads. Vanta 0.1 never stores credentials."""

from __future__ import annotations

from typing import Any

TOKEN_KEYS = {
    "token",
    "tokens",
    "access_token",
    "refresh_token",
    "id_token",
    "accessToken",
    "refreshToken",
    "idToken",
    "client_secret",
    "clientSecret",
    "msaToken",
    "minecraftToken",
}


def contains_token_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in TOKEN_KEYS or contains_token_key(item):
                return True
        return False
    if isinstance(value, list):
        return any(contains_token_key(item) for item in value)
    return False
