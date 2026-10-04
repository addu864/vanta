"""Dark home screen: saved servers, local account, and a user-picked background."""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from vanta.launcher.ui.app import VantaApp
from vanta.launcher.ui.pages import index_page
from vanta.launcher.ui.server import dispatch, make_server

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_home_markup_is_original_vanta() -> None:
    html = index_page()
    assert ">PLAY<" in html
    assert ">PROFILES<" in html
    assert ">MODS<" in html
    assert ">SKINS<" in html
    assert ">SERVERS<" in html
    assert ">SETTINGS<" in html
    assert 'data-tab="hud"' in html
    assert 'id="play"' in html
    assert 'id="create-profile"' in html
    assert 'id="add-instance"' in html
    assert 'id="server-start"' in html
    assert 'id="server-tunnel-manual"' in html
    assert 'id="pick-background"' in html
    assert "Where you left off" in html
    assert "Saved servers" in html
    assert "Add Microsoft account" in html
    assert "Sign in with Microsoft" not in html
    lowered = html.lower()
    assert "blueclient" not in lowered
    assert "bluemc" not in lowered
    assert "donutsmp" not in lowered


def test_background_is_stored_under_the_data_dir(tmp_path: Path) -> None:
    app = VantaApp(tmp_path)
    empty = app.background_state()
    assert empty["custom"] is False
    assert empty["url"] == "/assets/backgrounds/default.png"
    assert "scene" not in empty

    source = tmp_path / "picked.png"
    source.write_bytes(PNG)
    saved = app.set_background({"path": str(source)})
    assert saved["ok"] is True
    assert saved["custom"] is True
    stored = tmp_path / "background" / "custom.png"
    assert stored.is_file()
    assert stored.read_bytes() == PNG
    choice = json.loads((tmp_path / "background" / "choice.json").read_text(encoding="utf-8"))
    assert choice == {"file": "custom.png"}
    assert "scene" not in saved

    status, body, content_type = app.background_image()
    assert status == 200
    assert body == PNG
    assert content_type == "image/png"

    rejected = app.set_background({"path": str(tmp_path / "notes.txt")})
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    rejected = app.set_background({"path": str(tmp_path / "notes.txt")})
    assert rejected["ok"] is False
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"not really a png" + b"\x00" * 40)
    assert app.set_background({"path": str(fake)})["ok"] is False
    assert stored.read_bytes() == PNG

    cleared = app.clear_background()
    assert cleared["custom"] is False
    assert cleared["url"] == "/assets/backgrounds/default.png"
    assert not stored.exists()
    assert not (tmp_path / "background" / "choice.json").exists()
    status, _body, _kind = app.background_image()
    assert status == 404


def test_retired_world_choice_is_dropped_and_clear_returns_default(tmp_path: Path) -> None:
    app = VantaApp(tmp_path)
    folder = tmp_path / "background"
    folder.mkdir()
    (folder / "choice.json").write_text('{"scene": "nether"}\n', encoding="utf-8")
    state = app.background_state()
    assert state["custom"] is False
    assert state["url"] == "/assets/backgrounds/default.png"
    assert not (folder / "choice.json").exists()

    for scene in ("overworld", "nether", "end", "shift", "lobby"):
        rejected = app.set_scene({"scene": scene})
        assert rejected["ok"] is False
        assert rejected["custom"] is False
    assert not (folder / "choice.json").exists()
    assert app.background_state()["url"] == "/assets/backgrounds/default.png"

    source = tmp_path / "picked.png"
    source.write_bytes(PNG)
    saved = app.set_background({"path": str(source)})
    assert saved["custom"] is True
    (folder / "choice.json").write_text(
        '{"file": "custom.png", "scene": "end"}\n', encoding="utf-8"
    )
    kept = app.background_state()
    assert kept["custom"] is True
    choice = json.loads((folder / "choice.json").read_text(encoding="utf-8"))
    assert choice == {"file": "custom.png"}

    cleared = app.clear_background()
    assert cleared["custom"] is False
    assert cleared["url"] == "/assets/backgrounds/default.png"
    assert not (folder / "custom.png").exists()
    assert not (folder / "choice.json").exists()
    status, _body, _kind = app.background_image()
    assert status == 404


def test_background_browse_does_not_invent_an_image(tmp_path: Path) -> None:
    app = VantaApp(tmp_path)
    result = app.browse_background()
    assert result["ok"] is False
    assert result["custom"] is False
    assert "Windows" in result["error"]
    assert not (tmp_path / "background").exists()


def test_home_lists_saved_servers_and_remembers_a_visit(tmp_path: Path) -> None:
    app = VantaApp(tmp_path)
    first = app.home_state()
    assert first["recent"] == []
    assert first["saved"] == []
    created = app.create_server(
        {"name": "Night", "software": "paper", "eulaAccepted": True, "version": "1.21.11"}
    )
    assert created["ok"] is True
    server_id = created["server"]["id"]
    listed = app.home_state()
    assert listed["saved"][0]["name"] == "Night"
    assert listed["recent"][0]["id"] == server_id
    other = app.create_server(
        {"name": "Day", "software": "fabric", "eulaAccepted": True, "version": "1.21.11"}
    )
    remembered = app.remember_server({"serverId": other["server"]["id"]})
    assert remembered["recent"][0]["name"] == "Day"
    missing = app.remember_server({"serverId": "nope"})
    assert missing["ok"] is False


def test_http_serves_background_from_the_data_dir(tmp_path: Path) -> None:
    app = VantaApp(tmp_path)
    source = tmp_path / "picked.png"
    source.write_bytes(PNG)
    app.set_background({"path": str(source)})
    httpd = make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as response:
            html = response.read().decode("utf-8")
        assert ">PLAY<" in html
        with urllib.request.urlopen(base + "/api/background") as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["custom"] is True
        with urllib.request.urlopen(base + "/api/background/image") as response:
            assert response.read() == PNG
            assert response.headers.get("Content-Type") == "image/png"
    finally:
        httpd.shutdown()


def test_dispatch_rejects_a_non_image(tmp_path: Path) -> None:
    app = VantaApp(tmp_path)
    status, body, _kind = dispatch(
        app,
        "POST",
        "/api/background",
        json.dumps({"path": str(tmp_path / "missing.png")}).encode("utf-8"),
    )
    assert status == 400
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is False


def test_ui_glass_live_background_and_system_font(tmp_path: Path) -> None:
    """Frosted controls and one still backdrop. UI text is the system sans."""
    from vanta.shared.config.paths import repo_root

    html = index_page()
    assert 'id="live-bg"' in html
    assert "startLiveBackground" not in html
    assert "requestAnimationFrame" not in html
    assert "holdMs" not in html
    assert "fadeMs" not in html
    assert "Using the live Vanta background." not in html
    assert "Using the dark Vanta gradient." not in html
    for label in ("Overworld", "Nether", "End", "Shift"):
        assert ">" + label + "<" not in html
    for scene in ("overworld", "nether", "end", "shift"):
        assert 'data-scene="' + scene + '"' not in html
        assert "/assets/backgrounds/" + scene + ".jpg" not in html
    assert "/assets/backgrounds/default.png" in html
    assert "has-image" in html
    assert 'id="pick-background"' in html
    assert ">Custom image<" in html
    assert 'id="clear-background"' in html
    assert "One still image." in html
    assert "Vanta library" in html
    lowered = html.lower()
    assert "minecraft.ttf" not in lowered
    assert "mojang" not in lowered
    assert "press start" not in lowered
    assert "ely.by" in lowered
    css = (repo_root() / "assets" / "style.css").read_text(encoding="utf-8")
    assert "backdrop-filter" in css
    assert "@font-face" not in css
    assert "Press Start 2P" not in css
    assert "press-start-2p" not in css.lower()
    assert '"Segoe UI"' in css
    assert "system-ui" in css
    assert "linear-gradient(168deg" not in css
    assert "minmax(380px, 1.35fr)" in css
    assert ".library-cards" in css
    app = VantaApp(tmp_path)
    status, body, _content_type = dispatch(
        app, "GET", "/assets/fonts/press-start-2p-latin-400-normal.woff2", b""
    )
    assert status == 404
    assert b"wOF2" not in body[:4]
    status, body, content_type = dispatch(app, "GET", "/assets/backgrounds/default.png", b"")
    assert status == 200
    assert content_type == "image/png"
    assert body.startswith(b"\x89PNG\r\n\x1a\n")
    for scene in ("overworld.jpg", "nether.jpg", "end.jpg", "shift.jpg"):
        status, body, content_type = dispatch(app, "GET", "/assets/backgrounds/" + scene, b"")
        assert status == 404, scene
        assert body[:3] != b"\xff\xd8\xff"
    status, body, _content_type = dispatch(app, "GET", "/assets/backgrounds/../style.css", b"")
    assert status == 404
