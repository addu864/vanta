"""Vanta Friends service: session handling, friends/requests, presence heartbeat, world sharing."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from vanta.launcher.friends.client import EMAIL_RE, USERNAME_RE, FriendsError, SupabaseClient, load_project

E4MC_RE = re.compile(r"\b((?:[a-z0-9-]+\.)+e4mc\.link)\b", re.I)
HEARTBEAT_S = 30


def find_shared_address(instance: Path) -> str | None:
    """Newest e4all/e4mc public address in the instance's latest.log (set when the host opens to LAN)."""
    log = instance / "logs" / "latest.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")[-400_000:]
    except OSError:
        return None
    found = E4MC_RE.findall(text)
    if found and "Stopping singleplayer server" in text.rsplit(found[-1], 1)[-1]:
        return None  # the world was closed after it was shared
    return found[-1].lower() if found else None


class FriendsService:
    def __init__(self, data_dir: Path, *, client: Any = None, clock: Callable[[], float] = time.time) -> None:
        self.data_dir = Path(data_dir)
        self.folder = self.data_dir / "friends"
        self.session_path = self.folder / "session.json"
        self._clock = clock
        self._lock = threading.RLock()
        self._client = client
        self.autostart = client is None  # tests/mocks drive presence by hand
        self._config_error = ""
        self._heartbeat: threading.Thread | None = None
        self._presence: dict[str, Any] = {"status": "online", "playing": None, "game_version": None, "join_address": None}
        if client is None:
            try:
                project = load_project(self.data_dir)
                if project["url"] and project["anonKey"]:
                    self._client = SupabaseClient(project["url"], project["anonKey"])
            except FriendsError as exc:
                self._config_error = str(exc)

    # ---- feature flag ----
    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _need(self) -> Any:
        if self._client is None:
            raise FriendsError(self._config_error or "Friends is not switched on yet: no Supabase project is configured.")
        return self._client

    # ---- session ----
    def _read_session(self) -> dict[str, Any]:
        try:
            data = json.loads(self.session_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_session(self, data: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.session_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.session_path)

    def _store_auth(self, auth: dict[str, Any], email: str = "") -> dict[str, Any]:
        user = auth.get("user") or {}
        session = {"accessToken": auth.get("access_token"), "refreshToken": auth.get("refresh_token"),
                   "expiresAt": self._clock() + float(auth.get("expires_in") or 3600),
                   "userId": user.get("id"), "email": user.get("email") or email}
        self._write_session(session)
        return session

    def _token(self) -> tuple[str, str]:
        client = self._need()
        with self._lock:
            s = self._read_session()
            if not s.get("refreshToken"):
                raise FriendsError("Sign in to Friends first.")
            if self._clock() > float(s.get("expiresAt") or 0) - 60:
                try:
                    s = self._store_auth(client.refresh(s["refreshToken"]), s.get("email", ""))
                except FriendsError:
                    self.session_path.unlink(missing_ok=True)
                    raise FriendsError("Your Friends sign-in expired. Sign in again.")
            return str(s["accessToken"]), str(s["userId"])

    # ---- public API ----
    def state(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": True, "enabled": False, "reason": self._config_error or "Friends is coming soon: the server isn't connected yet."}
        s = self._read_session()
        out: dict[str, Any] = {"ok": True, "enabled": True, "signedIn": bool(s.get("refreshToken")), "email": s.get("email")}
        if out["signedIn"]:
            try:
                token, uid = self._token()
                prof = self._need().my_profile(uid, token)
                out["profile"] = {"username": prof.get("username"), "inviteCode": prof.get("invite_code")} if prof else None
            except FriendsError as exc:
                out.update(signedIn=bool(self._read_session().get("refreshToken")), error=str(exc))
        return out

    def register(self, email: str, password: str) -> dict[str, Any]:
        email = (email or "").strip()
        if not EMAIL_RE.match(email):
            raise FriendsError("Enter a real email address.")
        if len(password or "") < 8:
            raise FriendsError("Use a password of at least 8 characters.")
        auth = self._need().sign_up(email, password)
        if auth.get("access_token"):  # project has email confirmation turned off
            self._store_auth(auth, email)
            return {"ok": True, "verified": True}
        return {"ok": True, "verified": False, "message": f"Check {email} and click the verification link, then sign in."}

    def resend(self, email: str) -> dict[str, Any]:
        self._need().resend((email or "").strip())
        return {"ok": True}

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        auth = self._need().sign_in((email or "").strip(), password or "")
        self._store_auth(auth, email)
        self.start_heartbeat()
        return self.state()

    def sign_out(self) -> dict[str, Any]:
        s = self._read_session()
        if s.get("accessToken") and self._client is not None:
            try:
                self._client.sign_out(s["accessToken"])
            except FriendsError:
                pass
        self.session_path.unlink(missing_ok=True)
        return {"ok": True}

    def claim_username(self, username: str) -> dict[str, Any]:
        if not USERNAME_RE.match(username or ""):
            raise FriendsError("Usernames are 3–16 letters, numbers or _ (like your Minecraft name).")
        token, _ = self._token()
        row = self._need().rpc("claim_username", {"p_username": username}, token)
        row = row[0] if isinstance(row, list) and row else row
        return {"ok": True, "profile": {"username": (row or {}).get("username"), "inviteCode": (row or {}).get("invite_code")}}

    def add(self, target: str) -> dict[str, Any]:
        target = (target or "").strip()
        m = re.search(r"(?:invite[=/]|^)([A-Za-z0-9_]{3,16})$", target)
        if not m:
            raise FriendsError("Enter a Vanta username or an invite code.")
        token, _ = self._token()
        result = self._need().rpc("send_friend_request", {"p_target": m.group(1)}, token)
        return {"ok": True, "result": result}

    def respond(self, request_id: int, accept: bool) -> dict[str, Any]:
        token, _ = self._token()
        return {"ok": True, "result": self._need().rpc("respond_friend_request", {"p_request": int(request_id), "p_accept": bool(accept)}, token)}

    def remove(self, friend_id: str) -> dict[str, Any]:
        token, _ = self._token()
        return {"ok": True, "result": self._need().rpc("remove_friend", {"p_friend": str(friend_id)}, token)}

    def overview(self) -> dict[str, Any]:
        token, _ = self._token()
        client = self._need()
        friends = client.rpc("my_friends", {}, token) or []
        requests = client.rpc("my_requests", {}, token) or []
        return {"ok": True, "friends": friends, "requests": requests, "me": dict(self._presence)}

    # ---- presence ----
    def set_presence(self, **fields: Any) -> None:
        with self._lock:
            for k in ("status", "playing", "game_version", "join_address"):
                if k in fields:
                    self._presence[k] = fields[k]

    def beat(self) -> bool:
        try:
            token, uid = self._token()
            self._need().upsert_presence(uid, dict(self._presence), token)
            return True
        except FriendsError:
            return False

    def start_heartbeat(self) -> None:
        if not self.enabled or not self.autostart or (self._heartbeat and self._heartbeat.is_alive()):
            return

        def loop() -> None:
            while self._read_session().get("refreshToken"):
                self.beat()
                time.sleep(HEARTBEAT_S)

        self._heartbeat = threading.Thread(target=loop, daemon=True, name="vanta-friends-heartbeat")
        self._heartbeat.start()

    def share_world(self, instance: Path, game_version: str, address: str = "") -> dict[str, Any]:
        addr = (address or "").strip().lower() or find_shared_address(instance)
        if not addr:
            raise FriendsError("No e4all address yet. In Minecraft, press Esc → Open to LAN (Online Mode off), then try again.")
        if not re.fullmatch(r"[a-z0-9.-]{3,253}(:\d{1,5})?", addr):
            raise FriendsError("That doesn't look like a server address.")
        self.set_presence(status="playing", join_address=addr, game_version=game_version,
                          playing=f"Minecraft {game_version} (sharing a world)")
        self.beat()
        return {"ok": True, "address": addr}

    def stop_sharing(self) -> dict[str, Any]:
        self.set_presence(join_address=None, status="online", playing=None)
        self.beat()
        return {"ok": True}
