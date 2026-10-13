"""Supabase client for Vanta Friends: stdlib HTTPS only.

Auth goes through GoTrue (/auth/v1) and data through PostgREST (/rest/v1) RPC functions.
The Postgres schema lives in supabase/schema.sql. Only the public anon key and the
signed-in user's own session are used. RLS on the server enforces who can see what.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from vanta import __version__

PROJECT_FILE = Path(__file__).resolve().parent / "project.json"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


class FriendsError(Exception):
    pass


def load_project(data_dir: Path | None = None) -> dict[str, str]:
    """URL + anon key from env, then <data>/friends-project.json, then the shipped project.json."""
    url = os.environ.get("VANTA_SUPABASE_URL", "")
    key = os.environ.get("VANTA_SUPABASE_ANON_KEY", "")
    for path in ([data_dir / "friends-project.json"] if data_dir else []) + [PROJECT_FILE]:
        if url and key:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        url = url or str(data.get("url") or "")
        key = key or str(data.get("anonKey") or "")
    url = url.strip().rstrip("/")
    if url and not re.fullmatch(r"https://[a-z0-9-]+\.supabase\.co", url):
        url = ""  # only hosted Supabase projects over https
    if key and _jwt_role(key) == "service_role":
        raise FriendsError("A service_role key was configured. Use the public anon key only.")
    return {"url": url, "anonKey": key.strip()}


def _jwt_role(token: str) -> str:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("role") or "")
    except (IndexError, ValueError):
        return ""  # new-style publishable keys (sb_publishable_...) are not JWTs


class SupabaseClient:
    def __init__(self, url: str, anon_key: str, *, opener: Callable[..., Any] | None = None, timeout: float = 15.0) -> None:
        if not url or not anon_key:
            raise FriendsError("Friends is not set up yet.")
        if anon_key.startswith("sb_secret_"):
            raise FriendsError("A secret key was configured. Use the public anon/publishable key only.")
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self._opener = opener or urllib.request.urlopen
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Any = None, token: str | None = None,
              params: dict[str, str] | None = None) -> Any:
        url = self.url + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        headers = {"apikey": self.anon_key, "Authorization": f"Bearer {token or self.anon_key}",
                   "Accept": "application/json", "User-Agent": f"addu864/vanta/{__version__}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise FriendsError(_error_text(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FriendsError(f"Can't reach the Friends server ({getattr(exc, 'reason', exc)}).") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise FriendsError("The Friends server sent an unreadable answer.") from exc

    # ---- auth (GoTrue) ----
    def sign_up(self, email: str, password: str, redirect: str = "") -> dict[str, Any]:
        params = {"redirect_to": redirect} if redirect else None
        return self._call("POST", "/auth/v1/signup", {"email": email, "password": password}, params=params) or {}

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        return self._call("POST", "/auth/v1/token", {"email": email, "password": password},
                          params={"grant_type": "password"}) or {}

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._call("POST", "/auth/v1/token", {"refresh_token": refresh_token},
                          params={"grant_type": "refresh_token"}) or {}

    def resend(self, email: str) -> None:
        self._call("POST", "/auth/v1/resend", {"type": "signup", "email": email})

    def sign_out(self, token: str) -> None:
        self._call("POST", "/auth/v1/logout", {}, token=token)

    # ---- data (PostgREST RPC) ----
    def rpc(self, name: str, args: dict[str, Any], token: str) -> Any:
        if not re.fullmatch(r"[a-z_]{1,40}", name):
            raise FriendsError("bad function name")
        return self._call("POST", f"/rest/v1/rpc/{name}", args, token=token)

    def my_profile(self, user_id: str, token: str) -> dict[str, Any] | None:
        rows = self._call("GET", "/rest/v1/profiles", token=token,
                          params={"id": f"eq.{user_id}", "select": "id,username,invite_code"}) or []
        return rows[0] if rows else None

    def upsert_presence(self, user_id: str, row: dict[str, Any], token: str) -> None:
        url_params = {"on_conflict": "user_id"}
        body = {"user_id": user_id, **row, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        req_path = "/rest/v1/presence"
        url = self.url + req_path + "?" + urllib.parse.urlencode(url_params)
        headers = {"apikey": self.anon_key, "Authorization": f"Bearer {token}", "Content-Type": "application/json",
                   "Prefer": "resolution=merge-duplicates,return=minimal", "User-Agent": f"addu864/vanta/{__version__}"}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raise FriendsError(_error_text(exc)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise FriendsError(f"Can't reach the Friends server ({getattr(exc, 'reason', exc)}).") from exc


def _error_text(exc: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(exc.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        data = {}
    msg = str(data.get("msg") or data.get("message") or data.get("error_description") or data.get("error") or "")
    low = msg.lower()
    if "email not confirmed" in low:
        return "Verify your email first. Check your inbox for the Vanta link."
    if "invalid login" in low:
        return "Wrong email or password."
    if "already registered" in low:
        return "That email already has a Vanta account. Sign in instead."
    if "duplicate key" in low and "username" in low:
        return "That username is taken."
    if exc.code == 429:
        return "Too many tries. Wait a minute and try again."
    return msg[:200] or f"Friends server error (HTTP {exc.code})."
