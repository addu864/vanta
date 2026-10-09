"""Optional tunnel for a server this Vanta instance created.

The tunnel is off unless that server's vanta-server.json says enabled.
Vanta never downloads playit, cloudflared, or bore, never invents a public
address, and never points a tunnel at a host the user did not create here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

TOOL_NAMES = ("playit", "cloudflared", "bore")
TUNNEL_PID_NAME = "vanta-tunnel.pid"
ADDRESS_WAIT_SECONDS = 3.0
STATUSES = ("OFFLINE", "STARTING", "ONLINE", "FAILED")

TOOL_MISSING = (
    "Tunnel tool is not installed. "
    "Looked for playit, cloudflared, and bore with `command -v`. "
    "Vanta did not download a tunnel binary and did not invent a public address."
)
HOST_REFUSED = (
    "Vanta will not tunnel an arbitrary host. "
    "A tunnel can only target the local port of a server created in Vanta."
)
ENABLED_TYPE = "Tunnel enabled must be true or false."
FAILED_EXIT = "The tunnel process exited. Vanta did not keep or invent a public address."
NOT_STARTED = "The tunnel process was not started."
SIGN_IN = (
    "Playit is installed but needs you to sign in. "
    "Open the Playit app and sign in. Vanta did not invent a public address."
)
EMAIL_UNVERIFIED = (
    "Playit needs you to verify your email. "
    "Open the Playit app and verify it. Vanta did not invent a public address."
)
PLAYIT_NOT_RUNNING = (
    "Playit is installed but it is not running. "
    "Open the Playit app. Vanta did not invent a public address."
)
PLAYIT_NO_ADDRESS = (
    "Playit is running but it has no tunnel address yet. "
    "Open the Playit app, or paste a host:port below. "
    "Vanta did not invent a public address."
)
PLAYIT_PORT_MISMATCH = (
    "Playit is running but none of its tunnels target this server's port. "
    "Paste the host:port you want to share. Vanta did not invent a public address."
)
SAVED_ADDRESS_TYPE = "Tunnel address must look like host:port. Vanta did not invent one."
PLAYIT_PIPE = r"\\.\pipe\playitd-system"
PLAYIT_SOCKET = "/run/playit/playitd.sock"

# Last label must look like a hostname suffix, not a file extension we write.
_NOT_A_PUBLIC_SUFFIX = {
    "disabled",
    "jar",
    "json",
    "log",
    "md",
    "pid",
    "properties",
    "py",
    "sh",
    "tmp",
    "txt",
    "zip",
}
_ADDRESS_RE = re.compile(
    r"(?i)(?:(?:https?|tcp)://)?"
    r"([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)+)"
    r"(?::([0-9]{1,5}))?\b"
)
_ALLOWED_KEYS = {"serverId", "id", "enabled"}


class _Session:
    def __init__(self, process: subprocess.Popen[Any], tool: str, log_path: Path) -> None:
        self.process = process
        self.tool = tool
        self.log_path = log_path
        self.intentional_stop = False


class TunnelController:
    def __init__(self, servers: Any) -> None:
        self.servers = servers
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}

    def set_enabled(self, server_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return self._refused(ENABLED_TYPE)
        if "savedAddress" in payload:
            extra = set(payload) - {"serverId", "id", "savedAddress"}
            if extra:
                return self._refused(HOST_REFUSED)
            return self._save_address(server_id, payload.get("savedAddress"))
        extra = set(payload) - _ALLOWED_KEYS
        if extra:
            return self._refused(HOST_REFUSED)
        try:
            record = self.servers._require(server_id)
        except ValueError as exc:
            return self._refused(str(exc))
        enabled = payload.get("enabled")
        if enabled is False:
            self._write_enabled(record, False)
            self.stop(server_id)
            viewed = self.view(server_id)
            viewed["ok"] = True
            viewed["started"] = False
            return viewed
        if enabled is not True:
            return self._refused(ENABLED_TYPE)
        self._write_enabled(record, True)
        return self.start(server_id)

    def start(self, server_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                record = self.servers._require(server_id)
                folder = self.servers._folder(server_id)
            except ValueError as exc:
                return self._refused(str(exc))
            if not _enabled(record):
                self.stop(server_id)
                viewed = self._view_unlocked(server_id)
                viewed["ok"] = True
                viewed["started"] = False
                return viewed
            alive = self._alive_pid_unlocked(folder)
            if alive is not None:
                self._await_address_unlocked(server_id)
                viewed = self._view_unlocked(server_id)
                viewed["ok"] = viewed["status"] != "FAILED"
                viewed["started"] = False
                return viewed
            looked_up = _lookup_tools()
            chosen = next((item for item in looked_up if item["found"]), None)
            if chosen is None:
                self._clear_pid(folder)
                viewed = self._view_unlocked(server_id, looked_up=looked_up, error=TOOL_MISSING)
                viewed["ok"] = False
                viewed["started"] = False
                viewed["downloadedBinary"] = False
                return viewed
            try:
                port = _local_port(record.get("port"))
            except ValueError as exc:
                viewed = self._view_unlocked(server_id, looked_up=looked_up, error=str(exc))
                viewed["ok"] = False
                viewed["started"] = False
                return viewed
            binary = str(chosen["path"])
            tool = str(chosen["name"])
            if tool == "playit" and _is_playit_app(binary):
                _ensure_playit_app_running(binary)
                viewed = self._view_unlocked(server_id, looked_up=looked_up)
                viewed["started"] = viewed.get("status") == "ONLINE"
                viewed["downloadedBinary"] = False
                viewed["ok"] = viewed.get("status") == "ONLINE" and bool(viewed.get("address"))
                if viewed["status"] != "ONLINE":
                    viewed["address"] = None
                    viewed["started"] = False
                    viewed["ok"] = False
                return viewed
            try:
                argv = _argv(tool, binary, port)
            except ValueError as exc:
                viewed = self._view_unlocked(server_id, looked_up=looked_up, error=str(exc))
                viewed["ok"] = False
                viewed["started"] = False
                return viewed
            log_path = folder / "logs" / "tunnel.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("w", encoding="utf-8", buffering=1)
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=str(folder),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
            except FileNotFoundError:
                handle.close()
                viewed = self._view_unlocked(server_id, looked_up=looked_up, error=TOOL_MISSING)
                viewed["ok"] = False
                viewed["started"] = False
                viewed["downloadedBinary"] = False
                return viewed
            except Exception:
                handle.write(NOT_STARTED + "\n")
                handle.close()
                viewed = self._view_unlocked(server_id, looked_up=looked_up, error=NOT_STARTED)
                viewed["ok"] = False
                viewed["status"] = "FAILED"
                viewed["started"] = False
                viewed["address"] = None
                return viewed
            handle.close()
            if process.pid <= 1 or process.pid in {os.getpid(), os.getppid()}:
                viewed = self._view_unlocked(server_id, error=NOT_STARTED)
                viewed["ok"] = False
                viewed["status"] = "FAILED"
                viewed["address"] = None
                return viewed
            self._sessions[server_id] = _Session(process, tool, log_path)
            self._write_pid(folder, process.pid)
            self._await_address_unlocked(server_id)
            viewed = self._view_unlocked(server_id, looked_up=looked_up)
            viewed["started"] = viewed["status"] in {"STARTING", "ONLINE"}
            viewed["ok"] = viewed["status"] != "FAILED"
            viewed["downloadedBinary"] = False
            if viewed["status"] == "FAILED":
                viewed["error"] = FAILED_EXIT
                viewed["address"] = None
            return viewed

    def stop(self, server_id: str) -> dict[str, Any]:
        """Stop only the tunnel pid Vanta started for this server. Does not flip enabled."""
        with self._lock:
            try:
                folder = self.servers._folder(server_id)
                self.servers._require(server_id)
            except ValueError as exc:
                return self._refused(str(exc))
            session = self._sessions.get(server_id)
            if session is not None:
                session.intentional_stop = True
            pid = session.process.pid if session is not None else self._read_pid(folder)
            server_pid = self.servers._read_pid(folder)
            if pid and pid != server_pid and pid not in {os.getpid(), os.getppid()}:
                _kill_only(pid)
            if session is not None and session.process.poll() is None:
                try:
                    session.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            if pid:
                _reap(pid)
            still_alive = bool(pid and pid != server_pid and _pid_alive(pid))
            if still_alive:
                viewed = self._view_unlocked(server_id)
                viewed["ok"] = False
                viewed["stopped"] = False
                viewed["error"] = "The tunnel process is still alive."
                viewed["address"] = None
                if viewed["status"] == "ONLINE":
                    viewed["status"] = "STARTING"
                return viewed
            self._sessions.pop(server_id, None)
            self._clear_pid(folder)
            viewed = self._view_unlocked(server_id)
            viewed["ok"] = True
            viewed["stopped"] = True
            viewed["address"] = None
            viewed["status"] = "OFFLINE"
            viewed["error"] = None
            viewed.pop("error", None)
            return viewed

    def view(self, server_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                self.servers._require(server_id)
            except ValueError as exc:
                return self._refused(str(exc))
            return self._view_unlocked(server_id)

    def _await_address_unlocked(self, server_id: str) -> None:
        session = self._sessions.get(server_id)
        if session is None:
            return
        deadline = time.time() + ADDRESS_WAIT_SECONDS
        while time.time() < deadline:
            if session.process.poll() is not None:
                return
            if _extract_address(_read_log(session.log_path)):
                return
            time.sleep(0.05)

    def _view_unlocked(
        self,
        server_id: str,
        *,
        looked_up: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        record = self.servers._require(server_id)
        folder = self.servers._folder(server_id)
        enabled = _enabled(record)
        session = self._sessions.get(server_id)
        if session is not None and session.process.poll() is not None and not session.intentional_stop:
            self._write_pid(folder, session.process.pid)
        pid = self._read_pid(folder)
        alive = bool(pid and _pid_alive(pid))
        if session is not None and session.intentional_stop:
            alive = False
        tool = session.tool if session is not None else None
        address = None
        status = "OFFLINE"
        if not enabled:
            status = "OFFLINE"
            address = None
        elif session is not None and session.intentional_stop:
            status = "OFFLINE"
            address = None
        elif alive:
            text = _read_log(folder / "logs" / "tunnel.log")
            parsed = _extract_address(text)
            if parsed:
                status = "ONLINE"
                address = parsed
            else:
                status = "STARTING"
                address = None
        elif pid:
            status = "FAILED"
            address = None
            if error is None:
                error = FAILED_EXIT
        else:
            status = "OFFLINE"
            address = None
        if not enabled:
            status = "OFFLINE"
            address = None
            error = None
        if status != "ONLINE":
            address = None
        if looked_up is None and enabled and status == "OFFLINE" and error is None and not alive:
            looked_up = _lookup_tools()
            if not any(item["found"] for item in looked_up):
                error = TOOL_MISSING
        detected = None
        notice = None
        playit_error = None
        app_path = _find_playit_app()
        if app_path and not alive:
            picked = _pick_playit_address(_playit_ipc_lifecycle(), record.get("port"))
            detected = picked.get("address") or picked.get("shareAddress")
            notice = picked.get("notice")
            playit_error = picked.get("error")
            if enabled and picked.get("address") and status != "ONLINE":
                status = "ONLINE"
                address = picked.get("address")
                tool = "playit"
                error = None
            elif enabled and playit_error and not alive and status != "ONLINE" and error in {None, TOOL_MISSING, FAILED_EXIT}:
                error = playit_error
                status = "OFFLINE"
                address = None
        if not enabled:
            status = "OFFLINE"
            address = None
            error = None
        if status != "ONLINE":
            address = None
        saved_address = _saved_address(record)
        payload: dict[str, Any] = {
            "ok": error is None and status != "FAILED",
            "enabled": enabled,
            "status": status,
            "address": address,
            "savedAddress": saved_address,
            "detectedAddress": detected,
            "tool": tool if status in {"STARTING", "ONLINE"} else None,
            "pid": pid if alive else None,
            "toolsChecked": list(TOOL_NAMES),
            "downloadedBinary": False,
            "scope": "vanta-server-only",
        }
        if notice:
            payload["notice"] = notice
        if looked_up is not None:
            payload["toolsLookedUp"] = [
                {"name": item["name"], "found": item["found"]} for item in looked_up
            ]
        if error:
            payload["error"] = error
            payload["ok"] = False
        if status == "FAILED":
            payload["ok"] = False
            payload["address"] = None
        return payload

    def _alive_pid_unlocked(self, folder: Path) -> int | None:
        pid = self._read_pid(folder)
        if pid and _pid_alive(pid):
            return pid
        return None

    def _save_address(self, server_id: str, value: object) -> dict[str, Any]:
        try:
            record = self.servers._require(server_id)
        except ValueError as exc:
            return self._refused(str(exc))
        try:
            saved = _normalize_saved_address(value)
        except ValueError as exc:
            return self._refused(str(exc))
        tunnel = record.get("tunnel")
        enabled = isinstance(tunnel, dict) and tunnel.get("enabled") is True
        updated_tunnel: dict[str, Any] = {"enabled": enabled}
        if saved:
            updated_tunnel["savedAddress"] = saved
        updated = dict(record)
        updated["tunnel"] = updated_tunnel
        self.servers._write_record(updated)
        viewed = self.view(server_id)
        viewed["ok"] = True
        viewed["saved"] = True
        viewed["savedAddress"] = saved or None
        viewed["started"] = False
        return viewed

    def _write_enabled(self, record: dict[str, Any], enabled: bool) -> None:
        updated = dict(record)
        tunnel = record.get("tunnel")
        updated_tunnel: dict[str, Any] = {"enabled": bool(enabled)}
        if isinstance(tunnel, dict):
            saved = tunnel.get("savedAddress")
            if isinstance(saved, str) and saved:
                updated_tunnel["savedAddress"] = saved
        updated["tunnel"] = updated_tunnel
        self.servers._write_record(updated)

    def _write_pid(self, folder: Path, pid: int) -> None:
        (folder / TUNNEL_PID_NAME).write_text(f"{pid}\n", encoding="utf-8")

    def _read_pid(self, folder: Path) -> int | None:
        path = folder / TUNNEL_PID_NAME
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8").strip().splitlines()
        if not raw or not raw[0].isdigit():
            return None
        return int(raw[0])

    def _clear_pid(self, folder: Path) -> None:
        path = folder / TUNNEL_PID_NAME
        if path.is_file():
            path.unlink()

    def _refused(self, message: str) -> dict[str, Any]:
        return {
            "ok": False,
            "error": message,
            "enabled": False,
            "status": "OFFLINE",
            "address": None,
            "started": False,
            "downloadedBinary": False,
            "toolsChecked": list(TOOL_NAMES),
            "scope": "vanta-server-only",
        }


def _enabled(record: dict[str, Any]) -> bool:
    tunnel = record.get("tunnel")
    if not isinstance(tunnel, dict):
        return False
    return tunnel.get("enabled") is True


def _local_port(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Server port is not configured.")
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Server port is not configured.") from exc
    if port < 1 or port > 65535:
        raise ValueError("Server port is not configured.")
    return port


def _lookup_tools() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for name in TOOL_NAMES:
        path = _command_v(name)
        if name == "playit" and path is None:
            path = _find_playit_app()
        found.append({"name": name, "found": path is not None, "path": path})
    return found


def _command_v(name: str) -> str | None:
    if name not in TOOL_NAMES:
        return None
    try:
        completed = subprocess.run(
            ["sh", "-c", 'command -v "$1"', "vanta-command-v", name],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    candidate = Path(lines[0])
    if candidate.name != name or not candidate.is_file() or not os.access(candidate, os.X_OK):
        return None
    return str(candidate)


def _argv(tool: str, binary: str, port: int) -> list[str]:
    """Fixed argv. The only variable is this server's own local port."""
    if Path(binary).name != tool:
        raise ValueError(NOT_STARTED)
    local = f"127.0.0.1:{port}"
    if tool == "playit":
        return [binary, "--local", local]
    if tool == "cloudflared":
        return [binary, "tunnel", "--url", f"tcp://{local}", "--no-autoupdate"]
    if tool == "bore":
        return [binary, "local", str(port), "--to", "bore.pub", "--local-host", "127.0.0.1"]
    raise ValueError(NOT_STARTED)


def _extract_address(text: str) -> str | None:
    if not text:
        return None
    sample = text[-65536:]
    found: list[str] = []
    for match in _ADDRESS_RE.finditer(sample):
        host = match.group(1).rstrip(".").lower()
        port_text = match.group(2)
        if not _public_host(host):
            continue
        if port_text:
            port = int(port_text)
            if port < 1 or port > 65535:
                continue
            found.append(f"{host}:{port}")
        else:
            found.append(host)
    if not found:
        return None
    return found[-1]


def _public_host(host: str) -> bool:
    if not host or len(host) > 253 or ".." in host:
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return False
    suffix = labels[-1]
    if suffix in _NOT_A_PUBLIC_SUFFIX or not suffix.isalpha() or not 2 <= len(suffix) <= 24:
        return False
    if host in {"localhost", "localhost.localdomain", "broadcasthost"}:
        return False
    if host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _read_log(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _pid_alive(pid: int) -> bool:
    from vanta.launcher.servers.manager import _pid_alive as alive

    return alive(pid)


def _reap(pid: int) -> None:
    flag = getattr(os, "WNOHANG", None)
    if flag is None:
        return
    try:
        os.waitpid(pid, flag)
    except (ChildProcessError, OSError):
        return


def _kill_only(pid: int) -> None:
    """Signal one pid. Never a process group, so a server process is not included."""
    if pid <= 1 or pid == os.getpid() or pid == os.getppid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + 2
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    if not _pid_alive(pid):
        _reap(pid)
        return
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.time() + 1
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    _reap(pid)


def _saved_address(record: dict[str, Any]) -> str | None:
    tunnel = record.get("tunnel")
    if not isinstance(tunnel, dict):
        return None
    saved = tunnel.get("savedAddress")
    if not isinstance(saved, str) or not saved.strip():
        return None
    try:
        return _normalize_saved_address(saved) or None
    except ValueError:
        return None


def _normalize_saved_address(value: object) -> str:
    """Empty clears the saved address. Anything else must be a real host:port."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(SAVED_ADDRESS_TYPE)
    text = value.strip()
    if not text:
        return ""
    text = re.sub(r"^(?:https?|tcp)://", "", text, count=1, flags=re.IGNORECASE)
    parsed = _manual_address(text)
    if not parsed:
        raise ValueError(SAVED_ADDRESS_TYPE)
    return parsed


def _manual_address(text: str) -> str | None:
    if ":" not in text:
        host = text.strip().rstrip(".").lower()
        if _public_host(host):
            return host
        return None
    if text.count(":") != 1:
        return None
    host, port_text = text.rsplit(":", 1)
    host = host.strip().rstrip(".").lower()
    if not port_text.isdigit() or not _public_host(host):
        return None
    port = int(port_text)
    if port < 1 or port > 65535:
        return None
    return f"{host}:{port}"


def _find_playit_app() -> str | None:
    """Installed Playit desktop/CLI, not a name that only `command -v` would see."""
    candidates: list[Path] = []
    which = shutil.which("playit.exe")
    if which:
        candidates.append(Path(which))
    if os.name == "nt":
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA", "ProgramData"):
            raw = os.environ.get(key)
            if not raw:
                continue
            root = Path(raw)
            candidates.append(root / "playit_gg" / "bin" / "playit.exe")
            candidates.append(root / "Playit.gg" / "bin" / "playit.exe")
            candidates.append(root / "playit" / "playit.exe")
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = str(candidate)
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.is_file():
            return resolved
    return None


def _is_playit_app(path: str) -> bool:
    name = Path(path).name.lower()
    if name == "playit.exe":
        return True
    return "playit_gg" in str(Path(path)).lower().replace("\\", "/")


def _ensure_playit_app_running(binary: str) -> None:
    """Start the installed Playit service only if its IPC pipe is not already up.

    `playit start` returns after asking Windows to run the service. It does not
    register an account and it is not a Minecraft server.
    """
    if _playit_ipc_lifecycle() is not None:
        return
    try:
        subprocess.run(
            [binary, "start"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    deadline = time.time() + 3
    while time.time() < deadline:
        if _playit_ipc_lifecycle() is not None:
            return
        time.sleep(0.2)


def _playit_endpoints() -> list[str]:
    endpoints: list[str] = []
    if os.name == "nt":
        endpoints.append(PLAYIT_PIPE)
    if os.path.exists(PLAYIT_SOCKET):
        endpoints.append(PLAYIT_SOCKET)
    return endpoints


def _playit_ipc_lifecycle() -> dict[str, Any] | None:
    for endpoint in _playit_endpoints():
        line = _playit_ipc_get_state(endpoint)
        if not line:
            continue
        parsed = _parse_playit_state(line)
        if parsed is not None:
            return parsed
    return None


def _playit_ipc_get_state(endpoint: str) -> str | None:
    box: dict[str, str] = {}

    def work() -> None:
        try:
            if endpoint.startswith("/"):
                import socket

                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(2)
                client.connect(endpoint)
                try:
                    hello = _socket_line(client)
                    if not hello:
                        return
                    request = {"ipc_version": 2, "request_id": 1, "request": {"type": "get_state"}}
                    client.sendall((json.dumps(request) + "\n").encode("utf-8"))
                    line = _socket_line(client)
                finally:
                    client.close()
            else:
                with open(endpoint, "r+b", buffering=0) as pipe:
                    if not _pipe_line(pipe):
                        return
                    request = {"ipc_version": 2, "request_id": 1, "request": {"type": "get_state"}}
                    pipe.write((json.dumps(request) + "\n").encode("utf-8"))
                    line = _pipe_line(pipe)
            if line:
                box["line"] = line
        except OSError:
            return

    thread = threading.Thread(target=work, name="vanta-playit-ipc", daemon=True)
    thread.start()
    thread.join(2.5)
    return box.get("line")


def _pipe_line(pipe: Any) -> str:
    data = bytearray()
    while len(data) < 200000:
        chunk = pipe.read(1)
        if not chunk:
            break
        data.extend(chunk)
        if chunk == b"\n":
            break
    return data.decode("utf-8", "replace").strip()


def _socket_line(client: Any) -> str:
    data = bytearray()
    while len(data) < 200000:
        chunk = client.recv(1)
        if not chunk:
            break
        data.extend(chunk)
        if chunk == b"\n":
            break
    return data.decode("utf-8", "replace").strip()


def _parse_playit_state(line: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    response = data.get("response")
    if not isinstance(response, dict) or response.get("type") != "state":
        return None
    state = response.get("data")
    if not isinstance(state, dict):
        return None
    return state


def _pick_playit_address(lifecycle: dict[str, Any] | None, server_port: object) -> dict[str, Any]:
    if not lifecycle:
        if _find_playit_app():
            return {"address": None, "error": PLAYIT_NOT_RUNNING, "notice": None}
        return {"address": None, "error": None, "notice": None}
    state_name = str(lifecycle.get("state") or "")
    data = lifecycle.get("data") if isinstance(lifecycle.get("data"), dict) else {}
    if state_name in {"waiting_for_secret", "has_invalid_secret"}:
        return {"address": None, "error": SIGN_IN, "notice": None}
    if state_name != "running":
        return {"address": None, "error": PLAYIT_NOT_RUNNING, "notice": None}
    account = str(data.get("account_status") or "")
    notices: list[str] = []
    for item in data.get("notices") or []:
        if isinstance(item, dict) and isinstance(item.get("message"), str):
            message = item["message"].strip()
            if message:
                notices.append(message)
    notice = None
    if account == "email_not_verified" or any("email" in item.lower() for item in notices):
        notice = EMAIL_UNVERIFIED
    elif account == "guest":
        notice = SIGN_IN
    tunnels = [
        item
        for item in (data.get("tunnels") or [])
        if isinstance(item, dict) and not item.get("is_disabled")
    ]
    port = _port_or_none(server_port)
    chosen: dict[str, Any] | None = None
    if port is not None:
        for tunnel in tunnels:
            if _destination_port(tunnel.get("destination")) == port:
                chosen = tunnel
                break
    if chosen is not None:
        address = _display_address(chosen.get("display_address"))
        if address:
            return {"address": address, "error": None, "notice": notice}
    if len(tunnels) == 1:
        only = tunnels[0]
        only_address = _display_address(only.get("display_address"))
        dest_port = _destination_port(only.get("destination"))
        if only_address and port is not None and dest_port is not None and dest_port != port:
            detail = (
                f"Playit is running. Its tunnel address is {only_address}, "
                f"but that tunnel points at local port {dest_port}, not this server's port {port}."
            )
            if notice:
                detail = f"{detail} {notice}"
            else:
                detail = f"{detail} Vanta did not invent a public address."
            return {"address": None, "shareAddress": only_address, "error": detail, "notice": notice}
        if only_address:
            return {"address": only_address, "error": None, "notice": notice}
    if len(tunnels) > 1:
        return {"address": None, "error": PLAYIT_PORT_MISMATCH, "notice": notice}
    if account == "email_not_verified":
        return {"address": None, "error": notice or EMAIL_UNVERIFIED, "notice": notice}
    if account in {"guest", "unknown", ""}:
        return {"address": None, "error": SIGN_IN, "notice": notice}
    return {"address": None, "error": PLAYIT_NO_ADDRESS, "notice": notice}


def _display_address(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(char.isspace() for char in text):
        return None
    return _extract_address(text)


def _destination_port(value: object) -> int | None:
    if not isinstance(value, str) or ":" not in value:
        return None
    port_text = value.rsplit(":", 1)[-1].strip()
    if not port_text.isdigit():
        return None
    port = int(port_text)
    if port < 1 or port > 65535:
        return None
    return port


def _port_or_none(value: object) -> int | None:
    try:
        return _local_port(value)
    except ValueError:
        return None
