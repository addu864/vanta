"""In-memory stand-in for the Supabase project (used by tests and offline UI work).

Mirrors supabase/schema.sql: email must be confirmed before sign-in, usernames are unique,
friend requests auto-accept when both sides asked, presence is visible only to friends.
"""

from __future__ import annotations

import itertools
import secrets
import time
from typing import Any

from vanta.launcher.friends.client import FriendsError


class MockSupabase:
    def __init__(self, *, confirm_required: bool = True) -> None:
        self.confirm_required = confirm_required
        self.users: dict[str, dict[str, Any]] = {}      # email -> {id, password, confirmed}
        self.tokens: dict[str, str] = {}                 # access/refresh token -> user id
        self.profiles: dict[str, dict[str, Any]] = {}    # id -> {id, username, invite_code}
        self.requests: list[dict[str, Any]] = []
        self.friendships: set[tuple[str, str]] = set()
        self.presence: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self.now = time.time

    # test helper (the "click the email link" step)
    def confirm(self, email: str) -> None:
        self.users[email]["confirmed"] = True

    def _session(self, uid: str, email: str) -> dict[str, Any]:
        access, refresh = "acc-" + secrets.token_hex(6), "ref-" + secrets.token_hex(6)
        self.tokens[access] = uid
        self.tokens[refresh] = uid
        return {"access_token": access, "refresh_token": refresh, "expires_in": 3600, "user": {"id": uid, "email": email}}

    def _uid(self, token: str) -> str:
        uid = self.tokens.get(token)
        if not uid:
            raise FriendsError("JWT expired")
        return uid

    def sign_up(self, email: str, password: str, redirect: str = "") -> dict[str, Any]:
        if email in self.users:
            raise FriendsError("That email already has a Vanta account. Sign in instead.")
        uid = f"u{len(self.users) + 1}"
        self.users[email] = {"id": uid, "password": password, "confirmed": not self.confirm_required}
        return self._session(uid, email) if not self.confirm_required else {"id": uid, "email": email}

    def sign_in(self, email: str, password: str) -> dict[str, Any]:
        u = self.users.get(email)
        if not u or u["password"] != password:
            raise FriendsError("Wrong email or password.")
        if not u["confirmed"]:
            raise FriendsError("Verify your email first. Check your inbox for the Vanta link.")
        return self._session(u["id"], email)

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        uid = self._uid(refresh_token)
        email = next(e for e, u in self.users.items() if u["id"] == uid)
        return self._session(uid, email)

    def resend(self, email: str) -> None:
        return None

    def sign_out(self, token: str) -> None:
        self.tokens.pop(token, None)

    def my_profile(self, user_id: str, token: str) -> dict[str, Any] | None:
        self._uid(token)
        return self.profiles.get(user_id)

    def upsert_presence(self, user_id: str, row: dict[str, Any], token: str) -> None:
        if self._uid(token) != user_id:
            raise FriendsError("new row violates row-level security policy")
        self.presence[user_id] = {**row, "updated_at": self.now()}

    def _friends_of(self, uid: str) -> list[str]:
        return [b for a, b in self.friendships if a == uid]

    def rpc(self, name: str, args: dict[str, Any], token: str) -> Any:
        me = self._uid(token)
        if name == "claim_username":
            name_ = args["p_username"]
            if any(p["username"].lower() == name_.lower() and p["id"] != me for p in self.profiles.values()):
                raise FriendsError("That username is taken.")
            prof = self.profiles.setdefault(me, {"id": me, "invite_code": secrets.token_hex(4).upper()})
            prof["username"] = name_
            return prof
        if me not in self.profiles:
            raise FriendsError("claim a username first")
        if name == "send_friend_request":
            t = args["p_target"]
            other = next((p["id"] for p in self.profiles.values()
                          if p["username"].lower() == t.lower() or p["invite_code"] == t.upper()), None)
            if other is None:
                raise FriendsError("no Vanta user with that name or code")
            if other == me:
                raise FriendsError("that is you")
            if (me, other) in self.friendships:
                return "already friends"
            back = next((r for r in self.requests if r["from"] == other and r["to"] == me and r["status"] == "pending"), None)
            if back:
                return self.rpc("respond_friend_request", {"p_request": back["id"], "p_accept": True}, token)
            if not any(r["from"] == me and r["to"] == other and r["status"] == "pending" for r in self.requests):
                self.requests.append({"id": next(self._ids), "from": me, "to": other, "status": "pending", "created_at": self.now()})
            return "sent"
        if name == "respond_friend_request":
            r = next((r for r in self.requests if r["id"] == args["p_request"] and r["to"] == me and r["status"] == "pending"), None)
            if r is None:
                raise FriendsError("no such pending request")
            r["status"] = "accepted" if args["p_accept"] else "declined"
            if args["p_accept"]:
                self.friendships |= {(r["from"], r["to"]), (r["to"], r["from"])}
            return r["status"]
        if name == "remove_friend":
            f = args["p_friend"]
            self.friendships -= {(me, f), (f, me)}
            return "removed"
        if name == "my_friends":
            out = []
            for fid in self._friends_of(me):
                pr = self.presence.get(fid) or {}
                online = bool(pr) and self.now() - pr["updated_at"] < 90
                out.append({"id": fid, "username": self.profiles[fid]["username"], "online": online,
                            "status": pr.get("status") if online else "offline",
                            "playing": pr.get("playing") if online else None,
                            "game_version": pr.get("game_version") if online else None,
                            "join_address": pr.get("join_address") if online else None})
            return sorted(out, key=lambda f: (not f["online"], f["username"].lower()))
        if name == "my_requests":
            return [{"id": r["id"], "direction": "incoming" if r["to"] == me else "outgoing",
                     "username": self.profiles[r["from"] if r["to"] == me else r["to"]]["username"]}
                    for r in self.requests if r["status"] == "pending" and me in (r["from"], r["to"])]
        raise FriendsError(f"unknown function {name}")
