"""Vanta 0.5 optional tunnel tests.

No tunnel binary is downloaded. A missing tool must not invent an address.
PLAY stays a dry-run. LOCAL TEST ACCOUNT stays the only playable account.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from vanta.launcher.servers import tunnel as tunnel_module
from vanta.launcher.ui.app import VantaApp
from vanta.launcher.ui.pages import index_page
from vanta.launcher.ui.server import dispatch

PERFORMANCE = "Vanta Performance"


def _post(app: VantaApp, path: str, payload: dict) -> tuple[int, dict]:
    status, body, _content_type = dispatch(app, "POST", path, json.dumps(payload).encode("utf-8"))
    return status, json.loads(body.decode("utf-8"))


def _get(app: VantaApp, path: str) -> tuple[int, dict]:
    status, body, _content_type = dispatch(app, "GET", path, b"")
    return status, json.loads(body.decode("utf-8"))


def _create(app: VantaApp, name: str) -> dict:
    status, body = _post(
        app,
        "/api/servers/create",
        {"name": name, "software": "paper", "eulaAccepted": True, "version": "1.21.11"},
    )
    assert status == 200, body
    return body["server"]


def _alive(pid: int) -> bool:
    path = Path(f"/proc/{pid}/stat")
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    end = text.rfind(")")
    if end == -1 or end + 2 >= len(text):
        return False
    return text[end + 2] != "Z"


def _kill(pid: int | None) -> None:
    if not pid:
        return
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    for _ in range(20):
        if not _alive(pid):
            break
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass
    if _alive(pid):
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            return


def _install_fake(bindir: Path, source: str) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    path = bindir / "bore"
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _sleeping_fake(port_printer: bool = True) -> str:
    if port_printer:
        body = (
            "import sys, time\n"
            "port = next((arg for arg in sys.argv[1:] if arg.isdigit()), '0')\n"
            "print(f'vanta-mock-{port}.example.com', flush=True)\n"
            "time.sleep(60)\n"
        )
    else:
        body = "import time\nprint('127.0.0.1:25565', flush=True)\nprint('no public hostname', flush=True)\ntime.sleep(60)\n"
    return f"#!{sys.executable}\n{body}"


def test_wait_constant_is_a_few_seconds() -> None:
    assert tunnel_module.ADDRESS_WAIT_SECONDS >= 2


def test_tunnel_refused_for_unknown_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("tunnel started for an unknown server")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    missing = "ab" * 16
    status, body = _post(app, "/api/servers/tunnel", {"serverId": missing, "enabled": True})
    assert status == 400
    assert body["ok"] is False
    assert "No server" in body["error"]
    assert body["status"] == "OFFLINE"
    assert body["address"] is None
    status, body = _post(app, "/api/servers/tunnel", {"serverId": "../etc", "enabled": True})
    assert status == 400
    assert body["address"] is None
    status, body = _get(app, "/api/servers/tunnel?id=not-a-server")
    assert status == 400
    assert body["address"] is None
    assert not (tmp_path / "servers").exists()


def test_disabled_flag_starts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("disabled tunnel started a process")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    status, body = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": False})
    assert status == 200, body
    assert body["ok"] is True
    assert body["enabled"] is False
    assert body["status"] == "OFFLINE"
    assert body["address"] is None
    assert body["started"] is False
    status, body = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": "true"})
    assert status == 400
    assert body["address"] is None
    meta = json.loads((tmp_path / "servers" / server["id"] / "vanta-server.json").read_text(encoding="utf-8"))
    assert meta["tunnel"]["enabled"] is False
    assert not (tmp_path / "servers" / server["id"] / "vanta-tunnel.pid").exists()
    listed = app.list_managed_servers()["servers"][0]
    assert listed["tunnel"]["status"] == "OFFLINE"
    assert listed["tunnel"]["address"] is None


def test_arbitrary_host_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("arbitrary host started a tunnel")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    status, body = _post(
        app,
        "/api/servers/tunnel",
        {"serverId": server["id"], "enabled": True, "host": "evil.example", "port": 1, "command": "playit"},
    )
    assert status == 400
    assert body["ok"] is False
    assert body["status"] == "OFFLINE"
    assert body["address"] is None
    assert "arbitrary host" in body["error"]
    meta = json.loads((tmp_path / "servers" / server["id"] / "vanta-server.json").read_text(encoding="utf-8"))
    assert meta["tunnel"]["enabled"] is False


def test_missing_tool_is_honest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("missing tool tried to download or open a URL")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    status, body = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": True})
    assert status == 400, body
    assert body["ok"] is False
    assert body["status"] == "OFFLINE"
    assert body["address"] is None
    assert body["downloadedBinary"] is False
    assert "not installed" in body["error"].lower()
    assert "command -v" in body["error"]
    for name in ("playit", "cloudflared", "bore"):
        assert name in body["error"]
    looked = {item["name"]: item["found"] for item in body["toolsLookedUp"]}
    assert looked == {"playit": False, "cloudflared": False, "bore": False}
    blob = json.dumps(body)
    assert "trycloudflare" not in blob
    assert "bore.pub" not in blob
    assert body["address"] is None
    folder = tmp_path / "servers" / server["id"]
    assert not (folder / "vanta-tunnel.pid").exists()
    meta = json.loads((folder / "vanta-server.json").read_text(encoding="utf-8"))
    assert meta["tunnel"] == {"enabled": True}
    assert "address" not in meta["tunnel"]
    status, again = _get(app, f"/api/servers/tunnel?id={server['id']}")
    assert status == 400
    assert again["status"] == "OFFLINE"
    assert again["address"] is None
    assert "not installed" in again["error"].lower()


def test_mocked_tool_becomes_online_and_stop_clears_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")
    bindir = tmp_path / "bin"
    _install_fake(bindir, _sleeping_fake())
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("download")))
    folder = tmp_path / "servers" / server["id"]
    status, body = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": True})
    pid = body.get("pid")
    try:
        assert status == 200, body
        assert body["ok"] is True
        assert body["status"] == "ONLINE"
        assert body["address"] == f"vanta-mock-{server['port']}.example.com"
        assert body["tool"] == "bore"
        assert isinstance(pid, int)
        assert _alive(pid)
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
        assert str(server["port"]) in cmdline
        assert "127.0.0.1" in cmdline
        assert "evil" not in cmdline
        assert "bore.pub" in cmdline
        meta = json.loads((folder / "vanta-server.json").read_text(encoding="utf-8"))
        assert meta["tunnel"]["enabled"] is True
        assert "address" not in meta["tunnel"]
        saved = int((folder / "vanta-tunnel.pid").read_text(encoding="utf-8").strip())
        assert saved == pid
        status, stopped = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": False})
        assert status == 200, stopped
        assert stopped["status"] == "OFFLINE"
        assert stopped["address"] is None
        assert stopped["enabled"] is False
        assert not _alive(pid)
        assert not (folder / "vanta-tunnel.pid").exists()
        meta = json.loads((folder / "vanta-server.json").read_text(encoding="utf-8"))
        assert meta["tunnel"]["enabled"] is False
    finally:
        _kill(pid if isinstance(pid, int) else None)


def test_no_address_is_starting_not_invented(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")
    bindir = tmp_path / "bin"
    _install_fake(bindir, _sleeping_fake(port_printer=False))
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setattr(tunnel_module, "ADDRESS_WAIT_SECONDS", 0.3)
    status, body = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": True})
    pid = body.get("pid")
    try:
        assert status == 200, body
        assert body["status"] == "STARTING"
        assert body["address"] is None
        assert "trycloudflare" not in json.dumps(body)
        html_should_not = body["address"]
        assert html_should_not is None
    finally:
        _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": False})
        _kill(pid if isinstance(pid, int) else None)


def test_exited_tool_is_failed_without_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")
    bindir = tmp_path / "bin"
    _install_fake(
        bindir,
        f"#!{sys.executable}\nprint('vanta-dead.example.com', flush=True)\nraise SystemExit(1)\n",
    )
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    status, body = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": True})
    assert status == 400, body
    assert body["status"] == "FAILED"
    assert body["address"] is None
    assert "vanta-dead.example.com" not in json.dumps({k: v for k, v in body.items() if k != "error"})
    assert body.get("address") is None


def test_two_servers_do_not_share_tunnel_pids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    alpha = _create(app, "Alpha")
    beta = _create(app, "Beta")
    assert alpha["port"] != beta["port"]
    bindir = tmp_path / "bin"
    _install_fake(bindir, _sleeping_fake())
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    status, first = _post(app, "/api/servers/tunnel", {"serverId": alpha["id"], "enabled": True})
    assert status == 200, first
    status, second = _post(app, "/api/servers/tunnel", {"serverId": beta["id"], "enabled": True})
    assert status == 200, second
    pids = [first["pid"], second["pid"]]
    try:
        assert first["pid"] != second["pid"]
        assert first["address"] == f"vanta-mock-{alpha['port']}.example.com"
        assert second["address"] == f"vanta-mock-{beta['port']}.example.com"
        assert _alive(first["pid"]) and _alive(second["pid"])
        alpha_cmd = Path(f"/proc/{first['pid']}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
        beta_cmd = Path(f"/proc/{second['pid']}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
        assert str(alpha["port"]) in alpha_cmd
        assert str(beta["port"]) in beta_cmd
        assert str(beta["port"]) not in alpha_cmd
        status, stopped = _post(app, "/api/servers/stop", {"serverId": alpha["id"]})
        assert status == 200, stopped
        assert not _alive(first["pid"])
        assert _alive(second["pid"])
        status, alpha_tunnel = _get(app, f"/api/servers/tunnel?id={alpha['id']}")
        assert alpha_tunnel["status"] == "OFFLINE"
        assert alpha_tunnel["address"] is None
        alpha_meta = json.loads((tmp_path / "servers" / alpha["id"] / "vanta-server.json").read_text(encoding="utf-8"))
        assert alpha_meta["tunnel"]["enabled"] is True
        status, beta_tunnel = _get(app, f"/api/servers/tunnel?id={beta['id']}")
        assert status == 200, beta_tunnel
        assert beta_tunnel["status"] == "ONLINE"
        assert beta_tunnel["address"] == f"vanta-mock-{beta['port']}.example.com"
        assert beta_tunnel["pid"] == second["pid"]
    finally:
        for pid in pids:
            _post(app, "/api/servers/tunnel", {"serverId": alpha["id"], "enabled": False})
            _post(app, "/api/servers/tunnel", {"serverId": beta["id"], "enabled": False})
            _kill(pid)


def test_ui_hides_address_until_online() -> None:
    html = index_page()
    assert "Make this server accessible to friends outside your network?" in html
    assert "Milestone 1.0" in html
    assert 'id="server-tunnel-address-row" hidden' in html
    assert "Copy address" in html
    assert 'status === "ONLINE"' in html
    assert "trycloudflare" not in html
    assert "bore.pub" not in html


def test_local_play_dry_run_still_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    created = app.create_local_account({"username": "Advay"})
    assert created["account"]["label"] == "LOCAL TEST ACCOUNT"
    _create(app, "Lobby")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    calls: list[str] = []
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: calls.append("run"))
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: calls.append("popen"))
    result = app.play(
        {
            "accountId": created["account"]["id"],
            "versionNumber": "1.21.11",
            "loader": "fabric",
            "ramGb": 4,
            "profileName": PERFORMANCE,
            "targetMode": "local-dry-run",
        }
    )
    assert result["status"] == "Ready"
    assert result["launched"] is False
    assert result["gameStarted"] is False
    assert result["dryRun"] is True
    assert calls == []
    microsoft = app.begin_microsoft({})
    assert microsoft["authenticated"] is False
    assert microsoft["tokensStored"] is False


def test_saved_tunnel_address_is_entered_not_invented(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("saving an address started a process")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    status, bad = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "savedAddress": "not an address"})
    assert status == 400, bad
    assert bad["address"] is None
    assert bad["ok"] is False
    status, saved = _post(
        app,
        "/api/servers/tunnel",
        {"serverId": server["id"], "savedAddress": "friends.example.com:25565"},
    )
    assert status == 200, saved
    assert saved["savedAddress"] == "friends.example.com:25565"
    assert saved["address"] is None
    assert saved["status"] == "OFFLINE"
    meta = json.loads((tmp_path / "servers" / server["id"] / "vanta-server.json").read_text(encoding="utf-8"))
    assert meta["tunnel"]["savedAddress"] == "friends.example.com:25565"
    assert meta["tunnel"]["enabled"] is False
    status, cleared = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "savedAddress": ""})
    assert status == 200, cleared
    assert cleared["savedAddress"] is None
    meta = json.loads((tmp_path / "servers" / server["id"] / "vanta-server.json").read_text(encoding="utf-8"))
    assert "savedAddress" not in meta["tunnel"]
    status, refused = _post(
        app,
        "/api/servers/tunnel",
        {"serverId": server["id"], "savedAddress": "friends.example.com:25565", "host": "evil.example"},
    )
    assert status == 400, refused
    assert refused["address"] is None


def test_installed_playit_address_is_read_not_spawned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    binary = r"C:\Program Files\playit_gg\bin\playit.exe"
    monkeypatch.setattr(tunnel_module, "_find_playit_app", lambda: binary)
    monkeypatch.setattr(tunnel_module, "_command_v", lambda _name: None)

    def ipc() -> dict:
        return {
            "state": "running",
            "data": {
                "account_status": "email_not_verified",
                "notices": [{"message": "Please verify your email address"}],
                "tunnels": [
                    {
                        "display_address": "example-tunnel.tun.example.com",
                        "destination": "127.0.0.1:25565",
                        "is_disabled": False,
                    }
                ],
            },
        }

    monkeypatch.setattr(tunnel_module, "_playit_ipc_lifecycle", ipc)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("installed playit was spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    status, body = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": True})
    assert status == 200, body
    assert body["status"] == "ONLINE"
    assert body["address"] == "example-tunnel.tun.example.com"
    assert body["detectedAddress"] == "example-tunnel.tun.example.com"
    assert body["tool"] == "playit"
    assert body["downloadedBinary"] is False
    assert body["pid"] is None
    assert "verify your email" in body.get("notice", "").lower()
    assert not (tmp_path / "servers" / server["id"] / "vanta-tunnel.pid").exists()
    meta = json.loads((tmp_path / "servers" / server["id"] / "vanta-server.json").read_text(encoding="utf-8"))
    assert meta["tunnel"] == {"enabled": True}
    assert "address" not in meta["tunnel"]


def test_playit_without_address_does_not_invent_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = VantaApp(tmp_path)
    server = _create(app, "Lobby")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(tunnel_module, "_find_playit_app", lambda: r"C:\Program Files\playit_gg\bin\playit.exe")
    monkeypatch.setattr(tunnel_module, "_command_v", lambda _name: None)
    monkeypatch.setattr(
        tunnel_module,
        "_playit_ipc_lifecycle",
        lambda: {"state": "waiting_for_secret", "data": {}},
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("spawned")))
    status, body = _post(app, "/api/servers/tunnel", {"serverId": server["id"], "enabled": True})
    assert status == 400, body
    assert body["address"] is None
    assert body["detectedAddress"] is None
    assert "sign in" in body["error"].lower()
    assert body["status"] == "OFFLINE"


def test_ui_has_manual_tunnel_field() -> None:
    html = index_page()
    assert 'id="server-tunnel-manual"' in html
    assert 'id="server-tunnel-save"' in html
    assert 'id="server-tunnel-address-row" hidden' in html
    assert 'status === "ONLINE"' in html
