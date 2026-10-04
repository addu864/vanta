"""Localhost HTTP server for the Vanta UI."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from vanta.launcher.ui.app import VantaApp
from vanta.launcher.ui.pages import index_page
from vanta.shared.config.paths import repo_root

JSON = "application/json; charset=utf-8"
TEXT = "text/plain; charset=utf-8"


class BadRequest(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def make_server(app: VantaApp, host: str, port: int) -> ThreadingHTTPServer:
    handler = _handler_for(app)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def serve() -> None:
    host = "127.0.0.1"
    preferred = int(os.environ.get("VANTA_PORT", "8765"))
    app = VantaApp()
    try:
        httpd = make_server(app, host, preferred)
    except OSError:
        httpd = make_server(app, host, 0)
    bound = httpd.server_address[1]
    print(f"Vanta 1.0 listening on http://{host}:{bound}/", flush=True)
    print(f"Data directory: {app.data_dir}", flush=True)
    print("PLAY starts a local Java process only when Java is on PATH and client.jar is already installed. A LOCAL TEST ACCOUNT is not sent to online-mode servers. No Microsoft token is created.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Vanta stopped.", flush=True)
    finally:
        httpd.server_close()


def _handler_for(app: VantaApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def log_message(self, format: str, *args: object) -> None:
            return

        def _dispatch(self, method: str) -> None:
            try:
                raw = self._read_body() if method == "POST" else b""
                status, body, content_type = dispatch(app, method, self.path, raw)
            except BadRequest as exc:
                app.log.error(exc.message)
                status, body, content_type = 400, _json_bytes({"ok": False, "error": exc.message}), JSON
            except Exception:
                message = "Something went wrong inside Vanta."
                app.log.error(message)
                status, body, content_type = 500, _json_bytes({"ok": False, "error": message}), JSON
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or "0")
            path = urlparse(self.path).path
            limit = 512 * 1024 if path == "/api/skins/import" else 65536
            if length < 0 or length > limit:
                raise BadRequest("That request is too large.")
            if length == 0:
                return b""
            return self.rfile.read(length)

    return Handler


def dispatch(app: VantaApp, method: str, raw_path: str, body: bytes | None) -> tuple[int, bytes, str]:
    path = urlparse(raw_path).path
    if method == "GET" and path == "/":
        return 200, index_page().encode("utf-8"), "text/html; charset=utf-8"
    if method == "GET" and path == "/assets/style.css":
        return _read_asset("style.css", "text/css; charset=utf-8")
    if method == "GET" and path == "/assets/wordmark.svg":
        return _read_asset("wordmark.svg", "image/svg+xml")
    if method == "GET" and path == "/assets/logo.jpg":
        return _read_asset("logo.jpg", "image/jpeg")
    if method == "GET" and path.startswith("/assets/backgrounds/"):
        return _read_scene_background(path)
    if method == "GET" and path == "/api/status":
        return _ok(app.status())
    if method == "GET" and path == "/api/versions":
        return _ok(app.versions())
    if method == "GET" and path == "/api/accounts":
        return _ok(app.list_accounts())
    if method == "GET" and path == "/api/profiles":
        return _ok(app.list_profiles())
    if method == "GET" and path == "/api/settings":
        return _ok(app.get_settings())
    if method == "GET" and path == "/api/logs":
        return _ok(app.logs())
    if method == "GET" and path == "/api/last-error":
        text = app.last_error()
        return 200, text.encode("utf-8"), TEXT
    if method == "GET" and path == "/api/updates":
        return _ok(app.updates())
    if method == "POST" and path == "/api/accounts/local":
        result = app.create_local_account(_payload(body))
        return _from_ok(result)
    if method == "POST" and path == "/api/accounts/microsoft":
        result = app.begin_microsoft(_payload(body))
        return _from_ok(result)
    if method == "POST" and path == "/api/accounts/select":
        result = app.select_account(_payload(body))
        return _from_ok(result)
    if method == "POST" and path == "/api/profiles":
        result = app.create_profile(_payload(body))
        return _from_ok(result)
    if method == "POST" and path == "/api/profiles/select":
        result = app.select_profile(_payload(body))
        return _from_ok(result)
    if method == "POST" and path == "/api/instances/add":
        result = app.add_instance_profile(_payload(body))
        return _from_ok(result)
    if method == "POST" and path == "/api/instances/browse":
        result = app.browse_instance_folder()
        return _from_ok(result)
    if method == "GET" and path == "/api/home":
        return _from_ok(app.home_state())
    if method == "POST" and path == "/api/home/visit":
        return _from_ok(app.remember_server(_payload(body)))
    if method == "GET" and path == "/api/background":
        return _from_ok(app.background_state())
    if method == "GET" and path == "/api/background/image":
        status, payload, content_type = app.background_image()
        return status, payload, content_type
    if method == "POST" and path == "/api/background":
        return _from_ok(app.set_background(_payload(body)))
    if method == "POST" and path == "/api/background/scene":
        return _from_ok(app.set_scene(_payload(body)))
    if method == "POST" and path == "/api/background/clear":
        return _from_ok(app.clear_background())
    if method == "POST" and path == "/api/background/browse":
        return _from_ok(app.browse_background())
    if method == "POST" and path == "/api/settings":
        result = app.update_settings(_payload(body))
        return _from_ok(result)
    if method == "POST" and path == "/api/play":
        result = app.play(_payload(body))
        status = 200 if result.get("status") in {"Ready", "Started"} else 400
        return status, _json_bytes(result), JSON
    if method == "GET" and path == "/api/mods/search":
        from urllib.parse import parse_qs, urlparse as _urlparse
        query = parse_qs(_urlparse(raw_path).query)
        payload = {
            "query": (query.get("q") or query.get("query") or [""])[0],
            "profileName": (query.get("profile") or query.get("profileName") or [None])[0],
            "limit": (query.get("limit") or ["20"])[0],
            "offset": (query.get("offset") or ["0"])[0],
        }
        return _from_ok(app.search_mods(payload))
    if method == "POST" and path == "/api/mods/search":
        return _from_ok(app.search_mods(_payload(body)))
    if method == "GET" and path == "/api/mods/installed":
        from urllib.parse import parse_qs, urlparse as _urlparse
        query = parse_qs(_urlparse(raw_path).query)
        payload = {"profileName": (query.get("profile") or query.get("profileName") or [None])[0]}
        return _from_ok(app.list_installed_mods(payload))
    if method == "POST" and path == "/api/mods/installed":
        return _from_ok(app.list_installed_mods(_payload(body)))
    if method == "POST" and path == "/api/mods/details":
        return _from_ok(app.mod_details(_payload(body)))
    if method == "POST" and path == "/api/mods/install":
        return _from_ok(app.install_mod(_payload(body)))
    if method == "POST" and path == "/api/mods/remove":
        return _from_ok(app.remove_mod(_payload(body)))
    if method == "POST" and path == "/api/mods/enable":
        return _from_ok(app.set_mod_enabled(_payload(body), enabled=True))
    if method == "POST" and path == "/api/mods/disable":
        return _from_ok(app.set_mod_enabled(_payload(body), enabled=False))
    if method == "GET" and path == "/api/skins/state":
        return _from_ok(app.skins_state({}))
    if method == "GET" and path == "/api/skins/search":
        from urllib.parse import parse_qs
        query = parse_qs(urlparse(raw_path).query)
        payload = {
            "query": (query.get("q") or query.get("query") or [""])[0],
            "filter": (query.get("filter") or ["all"])[0],
            "limit": (query.get("limit") or ["5"])[0],
        }
        return _from_ok(app.search_skins(payload))
    if method == "POST" and path == "/api/skins/search":
        return _from_ok(app.search_skins(_payload(body)))
    if method == "GET" and path == "/api/skins/favorites":
        return _from_ok(app.skins.favorites())
    if method == "GET" and path == "/api/skins/recent":
        return _from_ok(app.skins.recent())
    if method == "POST" and path == "/api/skins/favorite":
        return _from_ok(app.favorite_skin(_payload(body)))
    if method == "POST" and path == "/api/skins/view":
        return _from_ok(app.view_skin(_payload(body)))
    if method == "POST" and path == "/api/skins/model":
        return _from_ok(app.set_skin_model(_payload(body)))
    if method == "POST" and path == "/api/skins/import":
        return _from_ok(app.import_skin(_payload(body)))
    if method == "POST" and path == "/api/skins/apply":
        return _from_ok(app.apply_skin(_payload(body)))
    if method == "GET" and path == "/api/skins/texture":
        from urllib.parse import parse_qs
        query = parse_qs(urlparse(raw_path).query)
        skin_id = (query.get("id") or [""])[0]
        status, payload, content_type = app.skin_texture(skin_id)
        return status, payload, content_type
    if method == "GET" and path == "/api/servers":
        return _from_ok(app.list_managed_servers())
    if method == "POST" and path == "/api/servers/create":
        return _from_ok(app.create_server(_payload(body)))
    if method == "POST" and path == "/api/servers/start":
        return _from_ok(app.start_server(_payload(body)))
    if method == "POST" and path == "/api/servers/stop":
        return _from_ok(app.stop_server(_payload(body)))
    if method == "POST" and path == "/api/servers/restart":
        return _from_ok(app.restart_server(_payload(body)))
    if method == "GET" and path == "/api/servers/console":
        return _from_ok(app.server_console(_query_server(raw_path)))
    if method == "POST" and path == "/api/servers/console":
        return _from_ok(app.server_console(_payload(body)))
    if method == "POST" and path == "/api/servers/settings":
        return _from_ok(app.update_server_settings(_payload(body)))
    if method == "GET" and path == "/api/servers/properties":
        return _from_ok(app.server_properties(_query_server(raw_path)))
    if method == "POST" and path == "/api/servers/properties":
        return _from_ok(app.update_server_properties(_payload(body)))
    if method == "POST" and path == "/api/servers/backup":
        return _from_ok(app.backup_server(_payload(body)))
    if method == "GET" and path == "/api/servers/players":
        return _from_ok(app.server_players(_query_server(raw_path)))
    if method == "POST" and path == "/api/servers/players":
        return _from_ok(app.server_players(_payload(body)))
    if method == "GET" and path == "/api/servers/plugins":
        return _from_ok(app.list_server_plugins(_query_server(raw_path)))
    if method == "POST" and path == "/api/servers/plugins/install":
        return _from_ok(app.install_server_plugin(_payload(body)))
    if method == "POST" and path == "/api/servers/plugins/enable":
        return _from_ok(app.set_server_plugin_enabled(_payload(body), enabled=True))
    if method == "POST" and path == "/api/servers/plugins/disable":
        return _from_ok(app.set_server_plugin_enabled(_payload(body), enabled=False))
    if method == "POST" and path == "/api/servers/plugins/remove":
        return _from_ok(app.remove_server_plugin(_payload(body)))
    if method == "GET" and path == "/api/servers/tunnel":
        return _from_ok(app.server_tunnel(_query_server(raw_path)))
    if method == "POST" and path == "/api/servers/tunnel":
        return _from_ok(app.set_server_tunnel(_payload(body)))
    if method == "GET" and path == "/api/hud":
        return _from_ok(app.hud_state(_query_profile(raw_path)))
    if method == "POST" and path == "/api/hud/module":
        return _from_ok(app.update_hud_module(_payload(body)))
    if method == "POST" and path == "/api/hud/reset":
        return _from_ok(app.reset_hud(_payload(body)))
    if method == "POST" and path == "/api/hud/editor":
        return _from_ok(app.update_hud_editor(_payload(body)))
    if method == "POST" and path == "/api/hud/preset":
        return _from_ok(app.apply_hud_preset(_payload(body)))
    if method == "POST" and path == "/api/hud/visual":
        return _from_ok(app.update_hud_visual(_payload(body)))
    if method == "POST" and path == "/api/hud/qol":
        return _from_ok(app.update_hud_qol(_payload(body)))
    if method == "GET" and path == "/api/utilities":
        return _from_ok(app.utilities_state(_query_profile(raw_path)))
    if method == "POST" and path == "/api/utilities":
        return _from_ok(app.set_utility(_payload(body)))
    if method == "GET" and path == "/api/appearance":
        return _from_ok(app.appearance_state(_query_profile(raw_path)))
    if method == "POST" and path == "/api/appearance/pack":
        return _from_ok(app.set_resource_pack(_payload(body)))
    if method == "POST" and path == "/api/appearance/shader":
        return _from_ok(app.set_shader_preset(_payload(body)))
    if method == "GET" and path == "/api/controller":
        return _from_ok(app.controller_state(_query_profile(raw_path)))
    if method == "POST" and path == "/api/controller":
        return _from_ok(app.update_controller(_payload(body)))
    if path.startswith("/api/"):
        return 404, _json_bytes({"ok": False, "error": "That API is not part of Vanta 1.0."}), JSON
    return 404, b"That page is not part of Vanta 1.0.\n", TEXT



def _query_server(raw_path: str) -> dict[str, Any]:
    from urllib.parse import parse_qs
    query = parse_qs(urlparse(raw_path).query)
    server_id = (query.get("id") or query.get("serverId") or [""])[0]
    return {"serverId": server_id}


def _query_profile(raw_path: str) -> dict[str, Any]:
    from urllib.parse import parse_qs
    query = parse_qs(urlparse(raw_path).query)
    name = (query.get("profile") or query.get("profileName") or [""])[0]
    if not name:
        return {}
    return {"profileName": name}


def _payload(body: bytes | None) -> dict[str, Any]:
    if not body:
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BadRequest("Could not read that request. Send JSON.") from exc
    if not isinstance(data, dict):
        raise BadRequest("Could not read that request. Send JSON.")
    return data


def _ok(payload: dict[str, Any]) -> tuple[int, bytes, str]:
    return 200, _json_bytes(payload), JSON


def _from_ok(payload: dict[str, Any]) -> tuple[int, bytes, str]:
    status = 200 if payload.get("ok") is True else 400
    return status, _json_bytes(payload), JSON


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")



_SCENE_BACKGROUNDS = {
    "default.png": "image/png",
}


def _read_scene_background(path: str) -> tuple[int, bytes, str]:
    name = path.removeprefix("/assets/backgrounds/")
    content_type = _SCENE_BACKGROUNDS.get(name)
    if content_type is None or "/" in name or ".." in name:
        return 404, b"Missing Vanta asset.\n", TEXT
    return _read_asset(f"backgrounds/{name}", content_type)


def _read_asset(name: str, content_type: str) -> tuple[int, bytes, str]:
    path = repo_root() / "assets" / name
    if not path.is_file():
        return 404, b"Missing Vanta asset.\n", TEXT
    return 200, path.read_bytes(), content_type
