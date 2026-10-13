"""Vanta Friends: feature flag, email-verified auth, requests, presence, sharing and joining (mocked Supabase)."""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest

from vanta.launcher.friends import client as C
from vanta.launcher.friends.client import FriendsError, SupabaseClient
from vanta.launcher.friends.mock import MockSupabase
from vanta.launcher.friends.service import FriendsService, find_shared_address

REPO = Path(__file__).resolve().parents[1]


def svc(tmp_path, backend, name):
    return FriendsService(tmp_path / name, client=backend)


def signed_up(tmp_path, backend, name, email):
    s = svc(tmp_path, backend, name)
    assert s.register(email, "password1")["verified"] is False
    with pytest.raises(FriendsError, match="Verify your email"):
        s.sign_in(email, "password1")
    backend.confirm(email)
    s.sign_in(email, "password1")
    s.claim_username(name)
    return s


def test_off_without_project(tmp_path, monkeypatch):
    monkeypatch.delenv("VANTA_SUPABASE_URL", raising=False)
    monkeypatch.delenv("VANTA_SUPABASE_ANON_KEY", raising=False)
    s = FriendsService(tmp_path)
    assert s.enabled is False
    st = s.state()
    assert st["ok"] and st["enabled"] is False
    with pytest.raises(FriendsError):
        s.register("a@b.co", "password1")


def test_shipped_project_file_has_no_secret():
    data = json.loads((REPO / "vanta/launcher/friends/project.json").read_text())
    assert set(data) <= {"_comment", "url", "anonKey"}
    values = json.dumps([data.get("url"), data.get("anonKey")])
    assert "sb_secret_" not in values and C._jwt_role(data.get("anonKey") or "") != "service_role"


def test_service_role_key_refused(tmp_path, monkeypatch):
    payload = base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode()).decode().rstrip("=")
    monkeypatch.setenv("VANTA_SUPABASE_URL", "https://abcd.supabase.co")
    monkeypatch.setenv("VANTA_SUPABASE_ANON_KEY", f"x.{payload}.y")
    with pytest.raises(FriendsError, match="anon key"):
        C.load_project(tmp_path)


def test_enabled_by_env(tmp_path, monkeypatch):
    payload = base64.urlsafe_b64encode(json.dumps({"role": "anon"}).encode()).decode().rstrip("=")
    monkeypatch.setenv("VANTA_SUPABASE_URL", "https://abcd.supabase.co")
    monkeypatch.setenv("VANTA_SUPABASE_ANON_KEY", f"x.{payload}.y")
    assert FriendsService(tmp_path).enabled


def test_non_supabase_url_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("VANTA_SUPABASE_URL", "http://evil.example")
    monkeypatch.setenv("VANTA_SUPABASE_ANON_KEY", "k")
    assert C.load_project(tmp_path)["url"] == ""


def test_request_accept_and_presence(tmp_path):
    b = MockSupabase()
    a = signed_up(tmp_path, b, "itz_alone10", "a@x.co")
    c = signed_up(tmp_path, b, "buddy", "b@x.co")
    assert a.add("buddy")["result"] == "sent"
    reqs = c.overview()["requests"]
    assert reqs[0]["direction"] == "incoming" and reqs[0]["username"] == "itz_alone10"
    assert c.respond(reqs[0]["id"], True)["result"] == "accepted"
    assert [f["username"] for f in a.overview()["friends"]] == ["buddy"]
    assert a.overview()["friends"][0]["online"] is False
    c.set_presence(status="playing", playing="Minecraft 1.21.11")
    assert c.beat()
    f = a.overview()["friends"][0]
    assert f["online"] and f["playing"] == "Minecraft 1.21.11"


def test_decline_and_invite_code_and_mutual(tmp_path):
    b = MockSupabase()
    a = signed_up(tmp_path, b, "alpha", "a@x.co")
    c = signed_up(tmp_path, b, "charlie", "c@x.co")
    code = c.state()["profile"]["inviteCode"]
    a.add(f"vanta://invite/{code}")
    rid = c.overview()["requests"][0]["id"]
    assert c.respond(rid, False)["result"] == "declined"
    assert a.overview()["friends"] == []
    a.add("charlie")
    assert c.add("alpha")["result"] == "accepted"  # both asked -> friends
    assert a.overview()["friends"][0]["username"] == "charlie"
    a.remove(a.overview()["friends"][0]["id"])
    assert c.overview()["friends"] == []


def test_username_rules(tmp_path):
    b = MockSupabase()
    signed_up(tmp_path, b, "taken", "t@x.co")
    s = svc(tmp_path, b, "other")
    s.register("o@x.co", "password1")
    b.confirm("o@x.co")
    s.sign_in("o@x.co", "password1")
    with pytest.raises(FriendsError, match="3–16"):
        s.claim_username("a b")
    with pytest.raises(FriendsError, match="taken"):
        s.claim_username("TAKEN")
    with pytest.raises(FriendsError, match="no Vanta user"):
        s.claim_username("ok_name")
        s.add("nobody")


def test_register_validation(tmp_path):
    s = svc(tmp_path, MockSupabase(), "x")
    with pytest.raises(FriendsError, match="email"):
        s.register("nope", "password1")
    with pytest.raises(FriendsError, match="8 characters"):
        s.register("a@b.co", "short")


def test_session_refresh_and_signout(tmp_path):
    b = MockSupabase()
    now = [1000.0]
    s = FriendsService(tmp_path / "s", client=b, clock=lambda: now[0])
    s.register("r@x.co", "password1")
    b.confirm("r@x.co")
    s.sign_in("r@x.co", "password1")
    old = json.loads(s.session_path.read_text())["accessToken"]
    now[0] += 7200
    s.claim_username("refresher")
    assert json.loads(s.session_path.read_text())["accessToken"] != old
    s.sign_out()
    assert s.state()["signedIn"] is False


def test_share_world_and_detect_address(tmp_path):
    inst = tmp_path / "inst"
    (inst / "logs").mkdir(parents=True)
    log = inst / "logs" / "latest.log"
    log.write_text("[e4all] Local game hosted on domain [quiet-fox-42.ap.e4mc.link]\n")
    assert find_shared_address(inst) == "quiet-fox-42.ap.e4mc.link"
    b = MockSupabase()
    a = signed_up(tmp_path, b, "host1", "h@x.co")
    g = signed_up(tmp_path, b, "guest1", "g@x.co")
    a.add("guest1")
    g.respond(g.overview()["requests"][0]["id"], True)
    assert a.share_world(inst, "1.21.11")["address"] == "quiet-fox-42.ap.e4mc.link"
    f = g.overview()["friends"][0]
    assert f["join_address"] == "quiet-fox-42.ap.e4mc.link" and f["game_version"] == "1.21.11"
    log.write_text(log.read_text() + "[Server thread/INFO]: Stopping singleplayer server as player logged out\n")
    assert find_shared_address(inst) is None
    with pytest.raises(FriendsError, match="Open to LAN"):
        a.share_world(inst, "1.21.11")
    with pytest.raises(FriendsError):
        a.share_world(inst, "1.21.11", "bad address!")


def test_client_sends_anon_key_and_user_token():
    seen = []

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout=0):
        seen.append(req)
        return R(b'["ok"]')

    cl = SupabaseClient("https://abcd.supabase.co", "anon-key", opener=opener)
    cl.rpc("my_friends", {}, "user-jwt")
    req = seen[0]
    assert req.full_url == "https://abcd.supabase.co/rest/v1/rpc/my_friends"
    assert req.get_header("Apikey") == "anon-key" and req.get_header("Authorization") == "Bearer user-jwt"
    with pytest.raises(FriendsError):
        SupabaseClient("https://abcd.supabase.co", "sb_secret_123")


def test_app_api_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("VANTA_SUPABASE_URL", raising=False)
    from vanta.launcher.ui.app import VantaApp
    from vanta.launcher.ui.server import dispatch
    app = VantaApp(data_dir=tmp_path)
    code, body, _ = dispatch(app, "GET", "/api/friends/state", None)
    assert code == 200 and json.loads(body)["enabled"] is False
    code, body, _ = dispatch(app, "POST", "/api/friends/add", b'{"target":"x"}')
    assert code == 400


def test_app_join_friend(tmp_path, monkeypatch):
    from vanta.launcher.ui import app as appmod
    b = MockSupabase()
    a = appmod.VantaApp(data_dir=tmp_path / "a")
    a.friends = FriendsService(tmp_path / "a", client=b)
    a.friends.register("a@x.co", "password1"); b.confirm("a@x.co"); a.friends.sign_in("a@x.co", "password1")
    a.friends.claim_username("joiner")
    host = signed_up(tmp_path, b, "hoster", "h@x.co")
    host.add("joiner")
    a.friends.respond(a.friends.overview()["requests"][0]["id"], True)
    host.share_world(tmp_path, "1.21.11", "abc.eu.e4mc.link")
    monkeypatch.setattr(appmod, "list_versions", lambda d: [{"versionNumber": "1.21.11", "loader": "fabric", "supported": True}])
    captured = {}
    monkeypatch.setattr(a, "play", lambda body: captured.update(body) or {"status": "Started"})
    fid = a.friends.overview()["friends"][0]["id"]
    res = a.friends_call("join", {"id": fid})
    assert res["status"] == "Started" and captured["joinServer"]["host"] == "abc.eu.e4mc.link"
    assert captured["versionNumber"] == "1.21.11"


def test_schema_has_rls_everywhere():
    sql = (REPO / "supabase/schema.sql").read_text()
    for table in ("profiles", "friend_requests", "friendships", "presence"):
        assert f"alter table public.{table} enable row level security" in sql
    assert "service_role" not in sql


def test_ui_friends_tab():
    html = (REPO / "vanta/launcher/ui/index.html").read_text()
    assert 'data-tab="friends" id="tab-friends" hidden' in html and "/api/friends/overview" in html
