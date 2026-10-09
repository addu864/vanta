"""Create-server wizard: Windows-safe process helpers and server jar download.

No network: every PaperMC / Fabric request goes to a fake opener.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest

from vanta.launcher.servers import manager as manager_module
from vanta.launcher.servers import software
from vanta.launcher.servers import tunnel as tunnel_module
from vanta.launcher.ui.app import VantaApp
from vanta.launcher.ui.server import dispatch

JAR_BYTES = b"PK\x03\x04" + b"fake paper jar " * 400
JAR_SHA = hashlib.sha256(JAR_BYTES).hexdigest()
JAR_URL = "https://fill-data.papermc.io/v1/objects/abc/paper-1.21.11-132.jar"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _paper_builds(sha: str = JAR_SHA, url: str = JAR_URL) -> list[dict]:
    return [
        {"id": 140, "channel": "BETA", "downloads": {"server:default": {"name": "beta.jar", "url": url, "checksums": {"sha256": "0" * 64}}}},
        {"id": 132, "channel": "STABLE", "downloads": {"server:default": {"name": "paper-1.21.11-132.jar", "url": url, "checksums": {"sha256": sha}}}},
        {"id": 120, "channel": "STABLE", "downloads": {"server:default": {"name": "old.jar", "url": url, "checksums": {"sha256": "1" * 64}}}},
    ]


def _opener(builds: list[dict] | None = None, calls: list[str] | None = None):
    def fake(request, timeout=None):
        url = request.full_url
        if calls is not None:
            calls.append(url)
        if url.startswith("https://fill.papermc.io/v3/projects/paper/versions/"):
            if builds is None:
                raise urllib.error.HTTPError(url, 404, "nope", {}, None)
            return _Response(json.dumps(builds).encode())
        if url == JAR_URL:
            return _Response(JAR_BYTES)
        raise AssertionError(f"unexpected url {url}")

    return fake


def _post(app: VantaApp, path: str, payload: dict) -> tuple[int, dict]:
    status, body, _ = dispatch(app, "POST", path, json.dumps(payload).encode("utf-8"))
    return status, json.loads(body.decode("utf-8"))


def _get(app: VantaApp, path: str) -> tuple[int, dict]:
    status, body, _ = dispatch(app, "GET", path, b"")
    return status, json.loads(body.decode("utf-8"))


def _app(tmp_path: Path, opener=None) -> VantaApp:
    app = VantaApp(tmp_path)
    app.servers._opener = opener or _opener(_paper_builds())
    return app


def _create(app: VantaApp, **extra) -> tuple[int, dict]:
    payload = {"name": "Doom's Haven", "software": "paper", "version": "1.21.11", "ram": 2, "eulaAccepted": True}
    payload.update(extra)
    return _post(app, "/api/servers/create", payload)


def test_create_downloads_latest_stable_paper_jar_and_accepts_eula(tmp_path: Path) -> None:
    calls: list[str] = []
    app = _app(tmp_path, _opener(_paper_builds(), calls))
    status, body = _create(app, downloadJar=True)
    assert status == 200, body
    assert body["jar"]["ok"] is True
    assert body["jar"]["jar"]["build"] == 132
    assert body["server"]["jarInstalled"] is True
    folder = Path(body["server"]["directory"])
    assert (folder / "server.jar").read_bytes() == JAR_BYTES
    assert "eula=true" in (folder / "eula.txt").read_text()
    assert "online-mode=false" in (folder / "server.properties").read_text()
    assert (folder / "start.bat").is_file()
    assert not list(folder.glob("*.part"))
    record = json.loads((folder / "vanta-server.json").read_text())
    assert record["jar"]["sha256"] == JAR_SHA
    assert JAR_URL in calls


def test_create_without_download_flag_makes_no_network_call(tmp_path: Path) -> None:
    calls: list[str] = []
    app = _app(tmp_path, _opener(_paper_builds(), calls))
    status, body = _create(app)
    assert status == 200
    assert "jar" not in body
    assert calls == []


def test_bad_checksum_keeps_server_but_no_jar(tmp_path: Path) -> None:
    app = _app(tmp_path, _opener(_paper_builds(sha="f" * 64)))
    status, body = _create(app, downloadJar=True)
    assert status == 200
    assert body["created"] is True
    assert body["jar"]["ok"] is False
    assert "sha256" in body["warning"]
    folder = Path(body["server"]["directory"])
    assert not (folder / "server.jar").exists()
    assert not list(folder.glob("*.part"))


def test_unknown_version_reports_clean_error(tmp_path: Path) -> None:
    app = _app(tmp_path, _opener(None))
    status, body = _create(app, downloadJar=True, version="1.99.9")
    assert status == 200
    assert body["jar"]["ok"] is False
    assert "not available" in body["jar"]["error"]


def test_install_jar_endpoint_retries_download(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _status, body = _create(app)
    server_id = body["server"]["id"]
    status, result = _post(app, "/api/servers/install-jar", {"serverId": server_id})
    assert status == 200, result
    assert result["server"]["jarInstalled"] is True


def test_download_refuses_unexpected_host(tmp_path: Path) -> None:
    with pytest.raises(software.DownloadError):
        software.download({"url": "https://evil.example.com/paper.jar"}, tmp_path / "server.jar", _opener())
    with pytest.raises(software.DownloadError):
        software.download({"url": "http://fill-data.papermc.io/x.jar"}, tmp_path / "server.jar", _opener())


def test_listing_after_server_exit_works_without_wnohang(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Root cause: os.WNOHANG does not exist on Windows. Listing servers after a
    server process exited raised AttributeError -> 'Something went wrong inside Vanta.'"""
    app = _app(tmp_path)
    _status, body = _create(app)
    server_id = body["server"]["id"]
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    app.servers._processes[server_id] = process
    monkeypatch.delattr(os, "WNOHANG", raising=False)
    status, listed = _get(app, "/api/servers")
    assert status == 200, listed
    assert listed["servers"][0]["status"] == "offline"
    manager_module._reap(process.pid)
    tunnel_module._reap(process.pid)
    status, created = _create(app, name="Second")
    assert status == 200, created
    assert created["server"]["port"] == 25566


def test_stop_uses_taskkill_when_posix_process_groups_are_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(manager_module, "IS_WINDOWS", True)
    monkeypatch.setattr(manager_module, "_pid_alive", lambda pid: False)

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(manager_module.subprocess, "run", fake_run)
    manager_module._signal_stop(424242)
    assert calls == [["taskkill", "/PID", "424242", "/T", "/F"]]


def test_stop_types_stop_on_console_first(tmp_path: Path) -> None:
    app = _app(tmp_path)
    _status, body = _create(app)
    server_id = body["server"]["id"]
    script = "import sys\nfor line in sys.stdin:\n    if line.strip() == 'stop':\n        print('Saving worlds', flush=True)\n        break\n"
    process = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    app.servers._processes[server_id] = process
    app.servers._write_pid(app.servers.root / server_id, process.pid)
    status, result = _post(app, "/api/servers/stop", {"serverId": server_id})
    assert status == 200, result
    assert result["stopped"] is True
    assert process.wait(timeout=5) == 0
    assert b"Saving worlds" in process.stdout.read()


def test_internal_error_is_logged_with_traceback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import urllib.request

    from vanta.launcher.ui.server import make_server

    app = _app(tmp_path)

    def boom():
        raise RuntimeError("kaboom-detail")

    monkeypatch.setattr(app, "list_managed_servers", boom)
    httpd = make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/api/servers", timeout=5)
        assert caught.value.code == 500
    finally:
        httpd.shutdown()
        httpd.server_close()
    log = (tmp_path / "vanta.log").read_text()
    assert "kaboom-detail" in log
    assert "/api/servers" in log
