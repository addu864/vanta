"""Local server folders, settings, backups, and out-of-process start/stop.

The create-server wizard downloads the official server jar (PaperMC Fill API
for Paper, Fabric meta for Fabric, sha256-checked for Paper) only when asked
with downloadJar=true. A server is reported running only while its process is
alive. Process helpers work on Windows and POSIX. An optional tunnel, when
enabled, is a separate process and only for this server's local port.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from vanta.launcher.servers import software as server_software
from vanta.launcher.servers.tunnel import TUNNEL_PID_NAME, TunnelController
from vanta.shared.utilities.jsonio import read_json, write_json
from vanta.shared.utilities.ram import parse_ram

JAR_MISSING = "Server jar is not installed"
NOT_PAPER = "not a Paper server"
EULA_ERROR = "EULA must be accepted before creating a server. Nothing was created."
URL_INSTALL_ERROR = "Install-from-URL is not supported."
JAR_NAME = "server.jar"
PID_NAME = "vanta.pid"
META_NAME = "vanta-server.json"
DEFAULT_VERSION = "1.21.11"
DEFAULT_RAM = 2
DEFAULT_PORT = 25565
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")
ID_RE = re.compile(r"^[a-f0-9]{32}$")
PROPERTY_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
PLUGIN_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.jar$")

PLACEHOLDER_README = (
    "Placeholder only. Paper jars belong in plugins/. Fabric jars belong in mods/. "
    "Vanta does not download them.\n"
)
IS_WINDOWS = os.name == "nt"
STOP_WAIT_SECONDS = 25


class ServerManager:
    def __init__(self, data_dir: Path, opener: Any = None) -> None:
        self.data_dir = Path(data_dir)
        self._opener = opener
        self.root = self.data_dir / "servers"
        self._processes: dict[str, subprocess.Popen[Any]] = {}
        self.tunnels = TunnelController(self)

    def list_servers(self) -> dict[str, Any]:
        servers = [self._public(record) for record in self._records()]
        return {"ok": True, "servers": servers, "scope": "this-machine-only"}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("eulaAccepted") is not True:
            return {"ok": False, "error": EULA_ERROR, "created": False}
        try:
            name = _clean_name(payload.get("name"))
            version = _clean_version(payload.get("version", DEFAULT_VERSION))
            software = _clean_software(payload.get("software"))
            ram = _clean_ram(payload.get("ram", payload.get("ramGb", DEFAULT_RAM)))
            jvm_args = _clean_jvm_args(payload.get("jvmArgs", ""))
            port = self._allocate_port(_optional_port(payload.get("port")))
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "created": False}

        server_id = uuid.uuid4().hex
        folder = self.root / server_id
        folder.mkdir(parents=True, exist_ok=False)
        try:
            record = {
                "id": server_id,
                "name": name,
                "version": version,
                "software": software,
                "ram": ram,
                "port": port,
                "eulaAccepted": True,
                "jvmArgs": jvm_args,
                "tunnel": {"enabled": False},
            }
            self._write_record(record)
            self._write_properties(folder, self._default_properties(record))
            (folder / "eula.txt").write_text(
                "# Accepted in the Vanta create-server wizard.\n# https://aka.ms/MinecraftEULA\neula=true\n",
                encoding="utf-8",
            )
            placeholder = folder / "mods-or-plugins"
            placeholder.mkdir()
            (placeholder / "README.txt").write_text(PLACEHOLDER_README, encoding="utf-8")
            content_dir = folder / ("plugins" if software == "paper" else "mods")
            content_dir.mkdir()
            (folder / "logs").mkdir()
            (folder / "backups").mkdir()
            self._write_start_script(record)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        result: dict[str, Any] = {"ok": True, "created": True, "server": self._public(record)}
        if payload.get("downloadJar") is True:
            installed = self.install_jar(server_id)
            result["jar"] = installed
            result["server"] = self._public(record)
            if not installed.get("ok"):
                result["warning"] = installed.get("error")
        return result

    def install_jar(self, server_id: str) -> dict[str, Any]:
        """Download the official server jar into this server folder as server.jar."""
        try:
            folder = self._folder(server_id)
            record = self._require(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "installed": False}
        if self._running(server_id):
            return {"ok": False, "error": "Stop the server before replacing its jar.", "installed": False}
        software = str(record.get("software") or "")
        version = str(record.get("version") or DEFAULT_VERSION)
        try:
            info = server_software.resolve(software, version, self._opener)
            stats = server_software.download(info, folder / JAR_NAME, self._opener)
        except server_software.DownloadError as exc:
            self._append_log(server_id, f"Server jar was not installed: {exc}")
            return {"ok": False, "error": str(exc), "installed": False}
        updated = dict(record)
        updated["jar"] = {
            "name": info.get("name"),
            "build": info.get("build"),
            "sha256": stats["sha256"],
            "bytes": stats["bytes"],
            "source": "papermc" if software == "paper" else "fabricmc",
        }
        self._write_record(updated)
        self._append_log(server_id, f"Installed {info.get('name')} as {JAR_NAME} ({stats['bytes']} bytes).")
        return {"ok": True, "installed": True, "jar": updated["jar"], "server": self._public(updated)}

    def start(self, server_id: str) -> dict[str, Any]:
        try:
            folder = self._folder(server_id)
            record = self._require(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "running": False, "status": "offline"}
        if self._running(server_id):
            return {
                "ok": False,
                "error": "That server process is already running.",
                "running": True,
                "status": "online",
                "server": self._public(record),
            }
        jar = folder / JAR_NAME
        if not jar.is_file() or jar.stat().st_size <= 0:
            message = (
                "Server jar is not installed. Vanta did not download a Paper, Fabric, "
                "or Mojang server jar and did not start a process. "
                f"Place {JAR_NAME} in this server folder, then start again."
            )
            self._append_log(server_id, message)
            return {
                "ok": False,
                "error": JAR_MISSING,
                "running": False,
                "status": "offline",
                "started": False,
                "server": self._public(record),
            }
        command = self._java_command(record)
        command[0] = find_java() or command[0]
        log_path = self._log_path(server_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("a", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(folder),
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                close_fds=True,
                **_spawn_options(),
            )
        except FileNotFoundError:
            handle.write("Java was not found. The server process was not started.\n")
            handle.close()
            return {
                "ok": False,
                "error": "Java was not found. The server process was not started.",
                "running": False,
                "status": "offline",
                "started": False,
            }
        except Exception as exc:
            handle.write(f"The server process was not started: {exc}\n")
            handle.close()
            return {
                "ok": False,
                "error": "The server process was not started.",
                "running": False,
                "status": "offline",
                "started": False,
            }
        handle.close()
        self._processes[server_id] = process
        self._write_pid(folder, process.pid)
        deadline = time.time() + 0.2
        while time.time() < deadline and process.poll() is None:
            time.sleep(0.02)
        if process.poll() is not None:
            self._processes.pop(server_id, None)
            self._clear_pid(folder)
            self._append_log(server_id, "The server process exited immediately. It is not online.")
            return {
                "ok": False,
                "error": "The server process exited immediately. See the console log.",
                "running": False,
                "status": "offline",
                "started": False,
                "server": self._public(record),
            }
        return {
            "ok": True,
            "running": True,
            "status": "online",
            "started": True,
            "pid": process.pid,
            "server": self._public(record),
        }

    def stop(self, server_id: str) -> dict[str, Any]:
        try:
            folder = self._folder(server_id)
            record = self._require(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "running": False, "status": "offline"}
        self.tunnels.stop(server_id)
        process = self._processes.pop(server_id, None)
        if process is not None and process.poll() is None:
            _console_stop(process)
        pid = process.pid if process is not None and process.poll() is None else self._read_pid(folder)
        if pid and _pid_alive(pid):
            _signal_stop(pid)
        if process is not None and process.poll() is None:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        if pid:
            _reap(pid)
        still_alive = bool(pid and _pid_alive(pid))
        if still_alive:
            self._write_pid(folder, int(pid))
            return {
                "ok": False,
                "error": "The server process is still alive.",
                "running": True,
                "status": "online",
                "stopped": False,
                "server": self._public(record),
            }
        self._clear_pid(folder)
        if process is not None or pid:
            self._append_log(server_id, "Server process stopped.")
        return {
            "ok": True,
            "running": False,
            "status": "offline",
            "stopped": True,
            "server": self._public(record),
        }

    def restart(self, server_id: str) -> dict[str, Any]:
        stopped = self.stop(server_id)
        if stopped.get("running"):
            return stopped
        started = self.start(server_id)
        started["restarted"] = False
        started["stopped"] = stopped.get("stopped", False)
        return started

    def attach_process(self, server_id: str, pid: int) -> dict[str, Any]:
        """Record a live process id. Does not invent a running server for a dead pid."""
        try:
            folder = self._folder(server_id)
            record = self._require(server_id)
            number = int(pid)
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc) or "A process id is required.", "running": False, "status": "offline"}
        if number <= 1 or number in {os.getpid(), os.getppid()}:
            return {
                "ok": False,
                "error": "Refusing to track the launcher process.",
                "running": False,
                "status": "offline",
            }
        if not _pid_alive(number):
            self._clear_pid(folder)
            return {
                "ok": False,
                "error": "That process is not alive.",
                "running": False,
                "status": "offline",
                "server": self._public(record),
            }
        self._write_pid(folder, number)
        return {"ok": True, "running": True, "status": "online", "pid": number, "server": self._public(record)}

    def console(self, server_id: str) -> dict[str, Any]:
        try:
            self._require(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        path = self._log_path(server_id)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if len(text) > 65536:
            text = text[-65536:]
        return {"ok": True, "log": text, "path": str(path)}

    def update_settings(self, server_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            record = self._require(server_id)
            folder = self._folder(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        updated = dict(record)
        try:
            if "port" in payload and payload.get("port") is not None:
                port = _required_port(payload.get("port"))
                self._ensure_port_free(port, except_id=server_id)
                updated["port"] = port
            if "ram" in payload or "ramGb" in payload:
                raw = payload.get("ram", payload.get("ramGb"))
                updated["ram"] = _clean_ram(raw)
            if "jvmArgs" in payload:
                updated["jvmArgs"] = _clean_jvm_args(payload.get("jvmArgs"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self._write_record(updated)
        properties = self._read_properties(folder)
        properties["server-port"] = str(updated["port"])
        self._write_properties(folder, properties)
        self._write_start_script(updated)
        if updated.get("port") != record.get("port"):
            self._retarget_tunnel(server_id, updated)
        return {
            "ok": True,
            "server": self._public(updated),
            "restartRequired": self._running(server_id),
            "jvmArgsExecuted": False,
        }

    def properties(self, server_id: str) -> dict[str, Any]:
        try:
            folder = self._folder(server_id)
            self._require(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        data = self._read_properties(folder)
        return {"ok": True, "properties": data, "text": _format_properties(data)}

    def update_properties(self, server_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            record = self._require(server_id)
            folder = self._folder(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            if "text" in payload and payload.get("text") is not None:
                data = _parse_properties_text(str(payload.get("text")))
            else:
                data = self._read_properties(folder)
            incoming = payload.get("properties")
            if incoming is not None:
                if not isinstance(incoming, dict):
                    raise ValueError("Properties must be a key/value object.")
                for key, value in incoming.items():
                    _check_property(str(key), "" if value is None else str(value))
                    data[str(key)] = "" if value is None else str(value)
            if "server-port" not in data:
                data["server-port"] = str(record["port"])
            port = _required_port(data["server-port"])
            self._ensure_port_free(port, except_id=server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        data["server-port"] = str(port)
        self._write_properties(folder, data)
        previous_port = record.get("port")
        record = dict(record)
        record["port"] = port
        self._write_record(record)
        self._write_start_script(record)
        if port != previous_port:
            self._retarget_tunnel(server_id, record)
        return {
            "ok": True,
            "properties": data,
            "text": _format_properties(data),
            "appliedLive": False,
            "server": self._public(record),
        }

    def backup(self, server_id: str) -> dict[str, Any]:
        try:
            folder = self._folder(server_id)
            self._require(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        backups = folder / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        destination = backups / f"{stamp}.zip"
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(folder)
                if relative.parts[0] == "backups":
                    continue
                if path.name in {PID_NAME, TUNNEL_PID_NAME}:
                    continue
                archive.write(path, relative.as_posix())
        return {
            "ok": True,
            "path": str(destination),
            "name": destination.name,
            "excludedPid": True,
        }

    def players(self, server_id: str) -> dict[str, Any]:
        try:
            record = self._require(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        running = self._running(server_id)
        if not running:
            return {
                "ok": True,
                "status": "offline",
                "running": False,
                "players": [],
                "playersKnown": True,
                "server": self._public(record),
            }
        return {
            "ok": True,
            "status": "online",
            "running": True,
            "players": [],
            "playersKnown": False,
            "note": "Vanta does not query players in 0.4 and does not invent them.",
            "server": self._public(record),
        }

    def list_plugins(self, server_id: str) -> dict[str, Any]:
        try:
            record = self._require(server_id)
            folder = self._folder(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if record["software"] != "paper":
            return {"ok": False, "error": NOT_PAPER}
        plugins = folder / "plugins"
        plugins.mkdir(exist_ok=True)
        return {"ok": True, "plugins": _plugin_rows(plugins), "software": "paper"}

    def install_plugin(self, server_id: str, source: str) -> dict[str, Any]:
        try:
            record = self._require(server_id)
            folder = self._folder(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if record["software"] != "paper":
            return {"ok": False, "error": NOT_PAPER}
        text = str(source or "").strip()
        if not text:
            return {"ok": False, "error": "A local .jar path is required."}
        if _looks_like_url(text):
            return {"ok": False, "error": URL_INSTALL_ERROR}
        path = Path(text)
        if not path.is_file():
            return {"ok": False, "error": "That local plugin jar was not found."}
        if not PLUGIN_NAME_RE.fullmatch(path.name):
            return {"ok": False, "error": "Plugin file name must be a simple .jar name."}
        plugins = folder / "plugins"
        plugins.mkdir(exist_ok=True)
        destination = plugins / path.name
        shutil.copyfile(path, destination)
        return {"ok": True, "plugin": {"name": path.name, "enabled": True, "file": f"plugins/{path.name}"}}

    def set_plugin_enabled(self, server_id: str, name: str, enabled: bool) -> dict[str, Any]:
        try:
            record = self._require(server_id)
            folder = self._folder(server_id)
            jar_name = _plugin_jar_name(name)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if record["software"] != "paper":
            return {"ok": False, "error": NOT_PAPER}
        plugins = folder / "plugins"
        enabled_path = plugins / jar_name
        disabled_path = plugins / f"{jar_name}.disabled"
        if enabled:
            if enabled_path.is_file():
                return {"ok": True, "plugin": {"name": jar_name, "enabled": True}}
            if not disabled_path.is_file():
                return {"ok": False, "error": "That plugin jar was not found."}
            disabled_path.rename(enabled_path)
        else:
            if disabled_path.is_file() and not enabled_path.is_file():
                return {"ok": True, "plugin": {"name": jar_name, "enabled": False}}
            if not enabled_path.is_file():
                return {"ok": False, "error": "That plugin jar was not found."}
            enabled_path.rename(disabled_path)
        return {"ok": True, "plugin": {"name": jar_name, "enabled": enabled}}

    def remove_plugin(self, server_id: str, name: str) -> dict[str, Any]:
        try:
            record = self._require(server_id)
            folder = self._folder(server_id)
            jar_name = _plugin_jar_name(name)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if record["software"] != "paper":
            return {"ok": False, "error": NOT_PAPER}
        plugins = folder / "plugins"
        removed = False
        for candidate in (plugins / jar_name, plugins / f"{jar_name}.disabled"):
            if candidate.is_file() and candidate.parent.resolve() == plugins.resolve():
                candidate.unlink()
                removed = True
        if not removed:
            return {"ok": False, "error": "That plugin jar was not found."}
        return {"ok": True, "removed": jar_name}

    def _retarget_tunnel(self, server_id: str, record: dict[str, Any]) -> None:
        """Port changed. Stop a tunnel still bound to the previous port.

        Start again only when the saved desired-state is enabled. A missing
        tunnel tool stays an honest OFFLINE error and does not fail the save.
        """
        self.tunnels.stop(server_id)
        tunnel = record.get("tunnel")
        if isinstance(tunnel, dict) and tunnel.get("enabled") is True:
            self.tunnels.start(server_id)

    def set_tunnel(self, server_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.tunnels.set_enabled(server_id, payload)

    def tunnel_status(self, server_id: str) -> dict[str, Any]:
        return self.tunnels.view(server_id)

    def _records(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for folder in sorted(self.root.iterdir()):
            meta = folder / META_NAME
            if folder.is_dir() and meta.is_file():
                loaded = read_json(meta, {})
                if isinstance(loaded, dict) and loaded.get("id"):
                    records.append(loaded)
        return records

    def _require(self, server_id: str) -> dict[str, Any]:
        folder = self._folder(server_id)
        meta = folder / META_NAME
        if not meta.is_file():
            raise ValueError("No server with that id.")
        loaded = read_json(meta, {})
        if not isinstance(loaded, dict):
            raise ValueError("No server with that id.")
        return loaded

    def _folder(self, server_id: str) -> Path:
        if not isinstance(server_id, str) or not ID_RE.fullmatch(server_id):
            raise ValueError("No server with that id.")
        folder = (self.root / server_id).resolve()
        if folder.parent != self.root.resolve():
            raise ValueError("No server with that id.")
        return folder

    def _public(self, record: dict[str, Any]) -> dict[str, Any]:
        server_id = str(record["id"])
        running = self._running(server_id)
        folder = self.root / server_id
        jar = folder / JAR_NAME
        return {
            "id": server_id,
            "name": record.get("name"),
            "version": record.get("version"),
            "software": record.get("software"),
            "ram": record.get("ram"),
            "port": record.get("port"),
            "eulaAccepted": record.get("eulaAccepted") is True,
            "jvmArgs": record.get("jvmArgs") or "",
            "running": running,
            "status": "online" if running else "offline",
            "jarInstalled": jar.is_file() and jar.stat().st_size > 0,
            "directory": str(folder),
            "tunnel": self.tunnels.view(server_id),
        }

    def _running(self, server_id: str) -> bool:
        process = self._processes.get(server_id)
        if process is not None:
            if process.poll() is None:
                return True
            self._processes.pop(server_id, None)
            _reap(process.pid)
            try:
                self._clear_pid(self._folder(server_id))
            except ValueError:
                return False
            return False
        try:
            folder = self._folder(server_id)
        except ValueError:
            return False
        pid = self._read_pid(folder)
        if pid and _pid_alive(pid):
            return True
        if pid:
            self._clear_pid(folder)
        return False

    def _allocate_port(self, requested: int | None) -> int:
        if requested is None:
            used = self._used_ports()
            port = DEFAULT_PORT
            while port in used:
                port += 1
                if port > 65535:
                    raise ValueError("No free server port is available.")
            return port
        self._ensure_port_free(requested, except_id=None)
        return requested

    def _ensure_port_free(self, port: int, except_id: str | None) -> None:
        for record in self._records():
            if except_id and record.get("id") == except_id:
                continue
            if int(record.get("port") or 0) == port:
                raise ValueError(f"Port {port} is already used by another Vanta server.")

    def _used_ports(self) -> set[int]:
        return {int(record["port"]) for record in self._records() if record.get("port")}

    def _write_record(self, record: dict[str, Any]) -> None:
        folder = self.root / str(record["id"])
        write_json(folder / META_NAME, record)

    def _write_start_script(self, record: dict[str, Any]) -> None:
        folder = self.root / str(record["id"])
        command = self._java_command(record)
        quoted = " ".join(shlex.quote(part) for part in command)
        script = (
            "#!/bin/sh\n"
            "# Generated by Vanta. This script does not download Paper, Fabric, or Minecraft.\n"
            'cd "$(dirname "$0")" || exit 1\n'
            f"if [ ! -f {JAR_NAME} ]; then\n"
            f'  echo "{JAR_MISSING}" >&2\n'
            "  exit 1\n"
            "fi\n"
            f"exec {quoted}\n"
        )
        path = folder / "start.sh"
        path.write_text(script, encoding="utf-8")
        try:
            path.chmod(0o755)
        except OSError:
            pass
        batch = subprocess.list2cmdline(command)
        (folder / "start.bat").write_text(
            "@echo off\r\n"
            "rem Generated by Vanta.\r\n"
            'cd /d "%~dp0"\r\n'
            f"if not exist {JAR_NAME} (echo {JAR_MISSING} & exit /b 1)\r\n"
            f"{batch}\r\n",
            encoding="utf-8",
            newline="",
        )

    def _java_command(self, record: dict[str, Any]) -> list[str]:
        ram = int(record["ram"])
        command = ["java", f"-Xms{ram}G", f"-Xmx{ram}G"]
        command.extend(shlex.split(str(record.get("jvmArgs") or "")))
        # Port lives in server.properties. Do not invent a non-standard --port flag.
        command.extend(["-jar", JAR_NAME, "nogui"])
        return command

    def _default_properties(self, record: dict[str, Any]) -> dict[str, str]:
        motd = str(record["name"]).replace("\n", " ")
        return {
            "server-port": str(record["port"]),
            "motd": motd,
            "online-mode": "false",
            "max-players": "20",
            "level-name": "world",
            "enable-query": "false",
            "enable-rcon": "false",
        }

    def _read_properties(self, folder: Path) -> dict[str, str]:
        path = folder / "server.properties"
        if not path.is_file():
            return {}
        try:
            return _parse_properties_text(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def _write_properties(self, folder: Path, data: dict[str, str]) -> None:
        path = folder / "server.properties"
        temporary = path.with_suffix(".properties.tmp")
        temporary.write_text(_format_properties(data), encoding="utf-8")
        temporary.replace(path)

    def _log_path(self, server_id: str) -> Path:
        return self.root / server_id / "logs" / "console.log"

    def _append_log(self, server_id: str, message: str) -> None:
        path = self._log_path(server_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {message}\n")

    def _write_pid(self, folder: Path, pid: int) -> None:
        (folder / PID_NAME).write_text(f"{pid}\n", encoding="utf-8")

    def _read_pid(self, folder: Path) -> int | None:
        path = folder / PID_NAME
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8").strip().splitlines()
        if not raw or not raw[0].isdigit():
            return None
        return int(raw[0])

    def _clear_pid(self, folder: Path) -> None:
        path = folder / PID_NAME
        if path.is_file():
            path.unlink()


def _clean_name(value: object) -> str:
    name = str(value or "").strip()
    if not name or len(name) > 32:
        raise ValueError("Server name must be 1 to 32 characters.")
    if any(char in name for char in "/\\\x00"):
        raise ValueError("Server name cannot include a path separator.")
    return name


def _clean_version(value: object) -> str:
    text = str(value or "").strip() or DEFAULT_VERSION
    if not VERSION_RE.fullmatch(text):
        raise ValueError("Minecraft version must look like 1.21.11.")
    return text


def _clean_software(value: object) -> str:
    text = str(value or "").strip().lower()
    if text not in {"paper", "fabric"}:
        raise ValueError("Software must be Paper or Fabric.")
    return text


def _clean_ram(value: object) -> int:
    ok, ram, error = parse_ram(value)
    if not ok or ram is None:
        raise ValueError(error)
    return ram


def _clean_jvm_args(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("JVM arguments must be a string. They are stored and not executed until start.")
    if len(value) > 2000:
        raise ValueError("JVM arguments string is too long.")
    if "\n" in value or "\x00" in value or "\r" in value:
        raise ValueError("JVM arguments must be a single line.")
    try:
        shlex.split(value)
    except ValueError as exc:
        raise ValueError("JVM arguments could not be parsed. They were not executed.") from exc
    return value


def _optional_port(value: object) -> int | None:
    if value is None or value == "":
        return None
    return _required_port(value)


def _required_port(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Port must be an integer from 1 to 65535.")
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Port must be an integer from 1 to 65535.") from exc
    if port < 1 or port > 65535:
        raise ValueError("Port must be an integer from 1 to 65535.")
    return port


def _check_property(key: str, value: str) -> None:
    if not PROPERTY_KEY_RE.fullmatch(key):
        raise ValueError(f"Invalid server.properties key: {key}")
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError(f"Invalid server.properties value for {key}.")
    if len(value) > 1000:
        raise ValueError(f"Server property {key} is too long.")


def _parse_properties_text(text: str) -> dict[str, str]:
    if len(text) > 20000:
        raise ValueError("server.properties is too large.")
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError("Each server.properties line must be key=value.")
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        _check_property(key, value)
        parsed[key] = value
    return parsed


def _format_properties(data: dict[str, str]) -> str:
    lines = [
        "# Vanta local server.properties",
        "# online-mode defaults to false. LOCAL TEST ACCOUNT is not a Microsoft session.",
        "# Editing this file does not download server software.",
    ]
    for key in sorted(data):
        lines.append(f"{key}={data[key]}")
    return "\n".join(lines) + "\n"


def _looks_like_url(value: str) -> bool:
    lowered = value.lower()
    return "://" in lowered or lowered.startswith("www.")


def _plugin_jar_name(value: object) -> str:
    name = Path(str(value or "")).name
    if name.endswith(".disabled"):
        name = name[: -len(".disabled")]
    if not PLUGIN_NAME_RE.fullmatch(name):
        raise ValueError("Plugin file name must be a simple .jar name.")
    return name


def _plugin_rows(plugins: Path) -> list[dict[str, Any]]:
    if not plugins.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(plugins.iterdir()):
        if not path.is_file():
            continue
        if path.name.endswith(".jar"):
            rows.append({"name": path.name, "enabled": True, "file": f"plugins/{path.name}"})
        elif path.name.endswith(".jar.disabled"):
            visible = path.name[: -len(".disabled")]
            rows.append({"name": visible, "enabled": False, "file": f"plugins/{path.name}"})
    return rows


def _pid_alive(pid: int) -> bool:
    if pid <= 1 or pid == os.getpid():
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    stat_path = Path(f"/proc/{pid}/stat")
    if stat_path.is_file():
        try:
            text = stat_path.read_text(encoding="utf-8")
        except OSError:
            return False
        end = text.rfind(")")
        if end == -1 or end + 2 >= len(text):
            return False
        state = text[end + 2]
        if state == "Z":
            _reap(pid)
            return False
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_pid_alive(pid: int) -> bool:
    """True when the process exists and has not exited. Does not signal it.

    os.kill(pid, 0) raises WinError 87 on Windows and must not be used.
    """
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.GetExitCodeProcess.restype = ctypes.c_bool
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_bool
    process_query = 0x1000
    still_active = 259
    handle = kernel.OpenProcess(process_query, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return int(code.value) == still_active
    finally:
        kernel.CloseHandle(handle)


def _signal_stop(pid: int) -> None:
    if pid <= 1 or pid == os.getpid() or pid == os.getppid():
        return
    if IS_WINDOWS or not hasattr(os, "getpgid"):
        _windows_kill_tree(pid)
        return
    try:
        group = os.getpgid(pid)
    except ProcessLookupError:
        return
    same_group = group == os.getpgrp()
    try:
        if same_group:
            os.kill(pid, signal.SIGTERM)
        else:
            os.killpg(group, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + 2
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    if not _pid_alive(pid):
        _reap(pid)
        return
    try:
        if same_group:
            os.kill(pid, signal.SIGKILL)
        else:
            os.killpg(group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.time() + 1
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    _reap(pid)


def _reap(pid: int) -> None:
    """Collect a finished child on POSIX. No-op on Windows (no WNOHANG there)."""
    flag = getattr(os, "WNOHANG", None)
    if flag is None:
        return
    try:
        os.waitpid(pid, flag)
    except (ChildProcessError, OSError):
        return


def _windows_kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
            **_hidden_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    deadline = time.time() + 3
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.05)


def _console_stop(process: subprocess.Popen[Any]) -> None:
    """Ask the Minecraft server to save and stop by typing `stop` on its console."""
    stdin = getattr(process, "stdin", None)
    if stdin is None:
        return
    try:
        stdin.write(b"stop\n")
        stdin.flush()
    except (OSError, ValueError):
        return
    try:
        process.wait(timeout=STOP_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        return


def _hidden_window() -> dict[str, Any]:
    if not IS_WINDOWS:
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def _spawn_options() -> dict[str, Any]:
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def find_java() -> str | None:
    """java on PATH, else JAVA_HOME, else common Windows install folders."""
    found = shutil.which("java")
    if found:
        return found
    exe = "java.exe" if IS_WINDOWS else "java"
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / exe).is_file():
        return str(Path(home) / "bin" / exe)
    if IS_WINDOWS:
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for root in filter(None, roots):
            for vendor in ("Eclipse Adoptium", "Java", "Microsoft", "Zulu", "BellSoft", "Amazon Corretto"):
                base = Path(root) / vendor
                if not base.is_dir():
                    continue
                for candidate in sorted(base.glob("*/bin/java.exe"), reverse=True):
                    return str(candidate)
    return None
