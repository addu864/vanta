"""Application services behind the local UI."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from vanta.launcher.accounts.microsoft import begin_microsoft_sign_in
from vanta.launcher.appearance.manager import AppearanceManager
from vanta.launcher.controller.detect import detect_controllers
from vanta.launcher.controller.manager import ControllerManager
from vanta.launcher.accounts.store import AccountStore
from vanta.launcher.minecraft.launch import attempt_play
from vanta.launcher.minecraft.versions import get_version, list_versions
from vanta.launcher.hud.store import ClientUiStore
from vanta.launcher.settings.profiles import ProfileStore
from vanta.launcher.mods.manager import ModManager
from vanta.launcher.mods.modrinth import ModrinthRepository
from vanta.launcher.mods.browser import BrowserError, ModBrowser
from vanta.launcher.mods.repository import ModRepository, ModRepositoryError
from vanta.launcher.mods.utilities import UtilityManager
from vanta.launcher.skins.builtin import VantaSkinLibrary
from vanta.launcher.skins.manager import SkinManager
from vanta.launcher.skins.models import LOCAL_APPLY_NOTE
from vanta.launcher.skins.repository import SkinRepository
from vanta.launcher.servers.manager import ServerManager
from vanta.launcher.updates import github as updater
from vanta import __version__ as VANTA_VERSION
from vanta.shared.config.paths import default_data_dir, repo_root
from vanta.shared.config.store import ConfigStore
from vanta.shared.logging.log import VantaLog
from vanta.shared.utilities.guard import contains_token_key
from vanta.shared.utilities.jsonio import read_json, write_json
from vanta.shared.utilities.ram import parse_ram

TOKEN_REJECTED = "Vanta 0.1 does not accept or store tokens."


class VantaApp:
    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        mod_repository: ModRepository | None = None,
        skin_repository: SkinRepository | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log = VantaLog(self.data_dir)
        self.config = ConfigStore(self.data_dir)
        self.accounts = AccountStore(self.data_dir)
        self.profiles = ProfileStore(self.data_dir, default_ram=int(self.config.get()["defaultRamGb"]))
        self.mod_repository = mod_repository or ModrinthRepository()
        self.mods = ModManager(self.data_dir, self.mod_repository)
        self.browser = ModBrowser(self.data_dir / "cache" / "modrinth", curated_file=Path(os.environ.get("VANTA_CURATED_MODS", str(self.data_dir / "curated-mods.md"))))
        from vanta.launcher.friends.service import FriendsService
        self.friends = FriendsService(self.data_dir, name=self._friend_name)
        self.utilities = UtilityManager(self.data_dir, self.mod_repository)
        self.skin_repository = skin_repository or VantaSkinLibrary(data_dir=self.data_dir)
        self.skins = SkinManager(self.data_dir, self.skin_repository)
        self.servers = ServerManager(self.data_dir)
        self.client_ui = ClientUiStore(self.data_dir)
        self.appearance = AppearanceManager(self.data_dir)
        self.controller = ControllerManager(self.data_dir)

    def status(self) -> dict[str, Any]:
        settings = self.config.get()
        return {
            "name": "Vanta",
            "version": VANTA_VERSION,
            "milestone": "1.0",
            "status": "ok",
            "dataDir": str(self.data_dir),
            "defaultVersion": settings["defaultVersion"],
            "defaultLoader": settings["defaultLoader"],
            "modsRepository": self.mod_repository.describe(),
            "skinsRepository": self.skin_repository.describe(),
            "platform": self.platform_info(),
        }

    def platform_info(self) -> dict[str, Any]:
        from vanta.shared import macplatform
        info = dict(macplatform.current())
        info["intelIgpu"] = macplatform.intel_igpu_only()
        note = ""
        if info["mac"] and info["rosetta"]:
            note = ("Vanta is running under Rosetta on an Apple Silicon Mac. It works, but is slower: "
                    "in Finder, Get Info on Vanta.app and untick \"Open using Rosetta\".")
        elif info["mac"] and info["intel"] and 0 < macplatform.macos_major(info["macos"]) < 12:
            note = f"Vanta for Intel Macs supports macOS 12 Monterey or newer; this Mac runs macOS {info['macos']}."
        info["note"] = note
        return info

    def versions(self) -> dict[str, Any]:
        settings = self.config.get()
        items = list_versions(str(settings["gameDirectory"]))
        return {"versions": items}

    def _game_root(self) -> Path:
        return Path(str(self.config.get()["gameDirectory"]))

    def available_instances(self) -> dict[str, Any]:
        from vanta.launcher.minecraft import instances

        result = instances.available_versions()
        installed = {(i["versionNumber"], i["loader"]) for i in instances.installed_instances(self._game_root())}
        main = self._game_root() / "1.21.11-fabric" / "client.jar"
        for row in result.get("versions", []):
            row["installedFabric"] = (row["id"], "fabric") in installed or (row["id"] == "1.21.11" and main.is_file())
            row["installedVanilla"] = (row["id"], "vanilla") in installed
        result["jobs"] = instances.all_jobs()
        return result

    def install_instance(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from vanta.launcher.minecraft import instances

        body = payload or {}
        version = str(body.get("version") or "").strip()
        loader = str(body.get("loader") or "fabric").strip().lower()
        preset = str(body.get("preset") or "").strip().lower()
        mods = [str(m) for m in (body.get("mods") or []) if isinstance(m, str)]
        result = instances.start_install(self._game_root(), version, loader, preset=preset, extra_mods=mods,
                                         include_fast=bool(body.get("includeFast", True)))
        if result.get("ok"):
            self.log.info(f"Downloading Minecraft {version} ({loader}) into its own instance folder.")
        else:
            self.log.error(str(result.get("error")))
        return result

    def instance_job(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from vanta.launcher.minecraft import instances

        job_id = str((payload or {}).get("id") or "")
        data = instances.job(job_id)
        data.pop("trace", None)
        return {"ok": True, "job": data}

    def list_accounts(self) -> dict[str, Any]:
        settings = self.config.get()
        return {
            "accounts": self.accounts.list(),
            "selectedAccountId": settings.get("selectedAccountId"),
        }

    def create_local_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        if contains_token_key(payload):
            return {"ok": False, "error": TOKEN_REJECTED}
        try:
            account = self.accounts.create_local(str(payload.get("username") or ""))
        except ValueError as exc:
            self.log.error(str(exc))
            return {"ok": False, "error": str(exc)}
        self.config.update({"selectedAccountId": account["id"]})
        self.log.info(
            f"Created LOCAL TEST ACCOUNT '{account['username']}' "
            f"({account['uuid']}). mode=local. Not valid for online-mode servers."
        )
        return {"ok": True, "account": account}

    def begin_microsoft(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        # Secrets are refused and never written. The flow still cannot succeed.
        result = begin_microsoft_sign_in(
            client_id=body.get("clientId") if isinstance(body.get("clientId"), str) else None,
            client_secret=body.get("clientSecret") if isinstance(body.get("clientSecret"), str) else None,
        )
        self.log.error(str(result["error"]))
        return result

    def select_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        account = self.accounts.get(str(payload.get("accountId") or ""))
        if account is None:
            message = "No local account selected. Create a LOCAL TEST ACCOUNT before play."
            self.log.error(message)
            return {"ok": False, "error": message}
        self.config.update({"selectedAccountId": account["id"]})
        return {"ok": True, "account": account}

    def list_profiles(self) -> dict[str, Any]:
        settings = self.config.get()
        return {
            "profiles": self.profiles.list(),
            "selectedProfileName": settings.get("selectedProfileName"),
        }

    def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        if contains_token_key(payload):
            return {"ok": False, "error": TOKEN_REJECTED}
        # Client-supplied mod lists are ignored so a profile cannot claim installs.
        ram_raw = payload.get("ramGb", self.config.get()["defaultRamGb"])
        ok, ram, error = parse_ram(ram_raw)
        if not ok or ram is None:
            self.log.error(error)
            return {"ok": False, "error": error}
        try:
            profile = self.profiles.create(str(payload.get("name") or ""), ram)
        except ValueError as exc:
            self.log.error(str(exc))
            return {"ok": False, "error": str(exc)}
        self.config.update({"selectedProfileName": profile["name"]})
        self.log.info(f"Created profile '{profile['name']}' with {ram} GB RAM.")
        return {"ok": True, "profile": profile}

    def add_instance_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Remember an existing instance folder as a profile. Does not copy or download."""
        if contains_token_key(payload):
            return {"ok": False, "error": TOKEN_REJECTED}
        ram_raw = payload.get("ramGb", self.config.get()["defaultRamGb"])
        ok, ram, error = parse_ram(ram_raw)
        if not ok or ram is None:
            self.log.error(error)
            return {"ok": False, "error": error}
        try:
            profile = self.profiles.create_from_instance(
                str(payload.get("name") or ""),
                ram,
                str(payload.get("path") or payload.get("instanceDirectory") or ""),
            )
        except ValueError as exc:
            self.log.error(str(exc))
            return {"ok": False, "error": str(exc)}
        self.config.update({"selectedProfileName": profile["name"]})
        self.log.info(
            f"Added instance profile '{profile['name']}' at {profile.get('instanceDirectory')}."
        )
        return {"ok": True, "profile": profile}

    def browse_instance_folder(self) -> dict[str, Any]:
        """Open the Windows folder dialog. The desktop window uses its own dialog instead."""
        import os
        import subprocess

        if os.name != "nt":
            return {
                "ok": False,
                "path": "",
                "error": "The folder dialog opens from the Vanta window on Windows. No folder was added.",
            }
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
            "$dialog.Description = 'Select a Minecraft instance folder'; "
            "$dialog.ShowNewFolderButton = $false; "
            "$result = $dialog.ShowDialog(); "
            "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath }"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "path": "", "error": f"The folder dialog did not open: {exc}"}
        lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
        if not lines:
            return {"ok": False, "path": "", "error": "No folder was selected."}
        return {"ok": True, "path": lines[-1], "error": ""}

    def home_state(self) -> dict[str, Any]:
        """Saved servers on this machine, plus the ones opened most recently."""
        listed = self.servers.list_servers()
        servers = list(listed.get("servers") or [])
        by_id = {str(item.get("id")): item for item in servers}
        recent_ids = self._home_record().get("recentServerIds") or []
        recent = [by_id[item] for item in recent_ids if item in by_id][:4]
        if not recent:
            recent = servers[:3]
        return {"ok": True, "recent": recent, "saved": servers}

    def remember_server(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED}
        server_id = str(body.get("serverId") or body.get("id") or "")
        known = {str(item.get("id")) for item in (self.servers.list_servers().get("servers") or [])}
        if server_id not in known:
            return {"ok": False, "error": "No server with that id."}
        record = self._home_record()
        ids = [item for item in record.get("recentServerIds") or [] if item != server_id]
        ids.insert(0, server_id)
        record["recentServerIds"] = ids[:8]
        write_json(self.data_dir / "home.json", record)
        return self.home_state()

    def background_state(self) -> dict[str, Any]:
        self._forget_retired_world()
        chosen = self._background_file()
        if chosen is None:
            return {
                "ok": True,
                "custom": False,
                "url": _DEFAULT_BACKGROUND_URL,
            }
        return {
            "ok": True,
            "custom": True,
            "url": "/api/background/image",
            "name": chosen.name,
        }

    def set_scene(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """World backgrounds are no longer choices. Does not write choice.json."""
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED, "custom": False}
        return {
            "ok": False,
            "error": "Overworld, Nether, End, and Shift are not background choices.",
            "custom": False,
        }

    def background_image(self) -> tuple[int, bytes, str]:
        chosen = self._background_file()
        if chosen is None:
            return 404, b"No background image is saved.\n", "text/plain; charset=utf-8"
        kind = _IMAGE_TYPES.get(chosen.suffix.lower(), "application/octet-stream")
        return 200, chosen.read_bytes(), kind

    def set_background(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Copy a user-picked image into the Vanta data directory. Does not fetch a URL."""
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED, "custom": False}
        raw = str(body.get("path") or "")
        if not raw or raw.strip() != raw or "\x00" in raw:
            return {"ok": False, "error": "Choose an image file.", "custom": False}
        source = Path(raw)
        try:
            if not source.is_file():
                return {"ok": False, "error": "That image file was not found.", "custom": False}
            size = source.stat().st_size
        except OSError as exc:
            return {"ok": False, "error": f"That image could not be read: {exc}", "custom": False}
        suffix = source.suffix.lower()
        if suffix not in _IMAGE_TYPES:
            return {
                "ok": False,
                "error": "Use a PNG, JPEG, WEBP, GIF, or BMP image.",
                "custom": False,
            }
        if size < 32 or size > _BACKGROUND_MAX_BYTES:
            return {"ok": False, "error": "That image is empty or larger than 8 MB.", "custom": False}
        try:
            data = source.read_bytes()
        except OSError as exc:
            return {"ok": False, "error": f"That image could not be read: {exc}", "custom": False}
        if not _image_bytes(data, suffix):
            return {"ok": False, "error": "That file is not a readable image.", "custom": False}
        folder = self.data_dir / "background"
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("custom.*"):
            if old.is_file():
                old.unlink()
        dest = folder / ("custom" + suffix)
        dest.write_bytes(data)
        write_json(folder / "choice.json", {"file": dest.name})
        self.log.info("Saved a custom launcher background in the Vanta data directory.")
        return {
            "ok": True,
            "custom": True,
            "url": "/api/background/image",
            "name": dest.name,
        }

    def clear_background(self) -> dict[str, Any]:
        """Drop a custom image and any saved world. The default image returns."""
        folder = self.data_dir / "background"
        if folder.is_dir():
            self._remove_custom_images(folder)
            choice = folder / "choice.json"
            if choice.is_file():
                choice.unlink()
        self.log.info("Cleared the custom launcher background.")
        return self.background_state()

    def browse_background(self) -> dict[str, Any]:
        """Open the Windows file dialog, then store the chosen image under the data dir."""
        import os
        import subprocess

        if os.name != "nt":
            return {
                "ok": False,
                "custom": False,
                "error": "The image dialog opens from the Vanta window on Windows. No background was saved.",
            }
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$dialog = New-Object System.Windows.Forms.OpenFileDialog; "
            "$dialog.Title = 'Choose a Vanta background image'; "
            "$dialog.Filter = 'Images|*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp'; "
            "$dialog.CheckFileExists = $true; "
            "$result = $dialog.ShowDialog(); "
            "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.FileName }"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "custom": False, "error": f"The image dialog did not open: {exc}"}
        lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
        if not lines:
            return {"ok": False, "custom": False, "error": "No image was selected."}
        return self.set_background({"path": lines[-1]})

    def _home_record(self) -> dict[str, Any]:
        loaded = read_json(self.data_dir / "home.json", {})
        if not isinstance(loaded, dict):
            return {}
        ids = loaded.get("recentServerIds")
        if not isinstance(ids, list):
            loaded["recentServerIds"] = []
        else:
            loaded["recentServerIds"] = [str(item) for item in ids if isinstance(item, str)]
        return loaded

    def _forget_retired_world(self) -> None:
        """Delete choice.json when it only names Overworld, Nether, End, or Shift."""
        folder = self.data_dir / "background"
        choice_path = folder / "choice.json"
        if not choice_path.is_file():
            return
        choice = read_json(choice_path, {})
        if not isinstance(choice, dict):
            choice_path.unlink()
            return
        scene = choice.get("scene")
        if not isinstance(scene, str) or scene not in _RETIRED_SCENES:
            return
        name = choice.get("file")
        kept = None
        if isinstance(name, str) and name == Path(name).name and name.startswith("custom."):
            path = folder / name
            if path.suffix.lower() in _IMAGE_TYPES and path.is_file():
                kept = name
        if kept is None:
            choice_path.unlink()
            return
        if set(choice) != {"file"}:
            write_json(choice_path, {"file": kept})

    def _remove_custom_images(self, folder: Path) -> None:
        for old in folder.glob("custom.*"):
            if old.is_file():
                old.unlink()

    def _background_file(self) -> Path | None:
        folder = self.data_dir / "background"
        choice = read_json(folder / "choice.json", {})
        name = choice.get("file") if isinstance(choice, dict) else None
        if not isinstance(name, str) or name != Path(name).name or not name.startswith("custom."):
            return None
        path = folder / name
        if path.suffix.lower() not in _IMAGE_TYPES or not path.is_file():
            return None
        return path

    def select_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile = self.profiles.get(str(payload.get("name") or payload.get("profileName") or ""))
        if profile is None:
            message = "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
            self.log.error(message)
            return {"ok": False, "error": message}
        self.config.update({"selectedProfileName": profile["name"]})
        return {"ok": True, "profile": profile}

    def get_settings(self) -> dict[str, Any]:
        return {"settings": self.config.get()}

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if contains_token_key(payload):
            self.log.error(TOKEN_REJECTED)
            return {"ok": False, "error": TOKEN_REJECTED}
        current = self.config.get()
        updates: dict[str, Any] = {}
        if "language" in payload:
            if payload["language"] != "en":
                message = "Vanta 0.1 only includes English."
                self.log.error(message)
                return {"ok": False, "error": message}
            updates["language"] = "en"
        if "theme" in payload:
            if payload["theme"] != "dark":
                message = "Vanta 0.1 only includes the dark theme."
                self.log.error(message)
                return {"ok": False, "error": message}
            updates["theme"] = "dark"
        if "defaultVersion" in payload or "defaultLoader" in payload:
            version_number = str(payload.get("defaultVersion", current["defaultVersion"]))
            loader = str(payload.get("defaultLoader", current["defaultLoader"]))
            version = get_version(version_number, loader, str(current["gameDirectory"]))
            if version is None or version.get("supported") is not True:
                message = (
                    "Default version must be a fully supported registry entry. "
                    "Vanta 0.1 supports 1.21.11 with Fabric."
                )
                self.log.error(message)
                return {"ok": False, "error": message}
            updates["defaultVersion"] = version_number
            updates["defaultLoader"] = loader
        if "defaultRamGb" in payload:
            ok, ram, error = parse_ram(payload.get("defaultRamGb"))
            if not ok or ram is None:
                self.log.error(error)
                return {"ok": False, "error": error}
            updates["defaultRamGb"] = ram
        if "gameDirectory" in payload:
            path = str(payload.get("gameDirectory") or "").strip()
            if not path:
                message = "Game directory path is required."
                self.log.error(message)
                return {"ok": False, "error": message}
            if len(path) > 4096:
                message = "Game directory path is too long."
                self.log.error(message)
                return {"ok": False, "error": message}
            updates["gameDirectory"] = path
        if "jvmArgs" in payload:
            args = payload.get("jvmArgs")
            if not isinstance(args, str):
                message = "JVM arguments must be a string. They are stored and not executed."
                self.log.error(message)
                return {"ok": False, "error": message}
            if len(args) > 2000:
                message = "JVM arguments string is too long. They are stored and not executed."
                self.log.error(message)
                return {"ok": False, "error": message}
            updates["jvmArgs"] = args
        try:
            saved = self.config.update(updates)
        except ValueError as exc:
            self.log.error(str(exc))
            return {"ok": False, "error": str(exc)}
        self.log.info("Saved settings. JVM arguments were stored and not executed.")
        return {"ok": True, "settings": saved, "jvmArgsExecuted": False}

    def play(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        payload = dict(payload or {})
        try:
            folder, _ = self._instance_dir(payload)
            if folder is not None:
                ram = self._instance_settings(folder)["ramGb"]
                if ram:
                    payload["ramGb"] = ram
        except Exception:  # noqa: BLE001 - settings are optional
            pass
        return attempt_play(
            config=self.config,
            accounts=self.accounts,
            profiles=self.profiles,
            log=self.log,
            data_dir=self.data_dir,
            payload=payload,
        )

    def popular_servers(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from vanta.launcher.servers import popular

        servers = popular.load_servers(self.data_dir)
        live = str((payload or {}).get("live", "1")) != "0"
        rows = popular.statuses(servers) if live else [dict(s, address=popular.address(s), status={}) for s in servers]
        for row in rows:
            row["icon"] = f"/assets/servers/{row['id']}.png"
            st = row.get("status") or {}
            if not st.get("online"):
                # No reply (or no raw TCP here): show only the bundled info, never "offline".
                row["status"] = {"online": None, "checkedAt": st.get("checkedAt")}
        return {"ok": True, "servers": rows, "config": str(self.data_dir / popular.CONFIG_NAME)}

    def _installed_numbers(self, loader: str = "") -> list[str]:
        settings = self.config.get()
        rows = list_versions(str(settings["gameDirectory"]))
        return [str(v["versionNumber"]) for v in rows if v.get("supported") and (not loader or v.get("loader") == loader)]

    def join_popular_server(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Add the server to the instance's servers.dat, then PLAY straight into it."""
        from vanta.launcher.servers import popular

        body = dict(payload or {})
        server = next((s for s in popular.load_servers(self.data_dir) if s["id"] == str(body.get("id") or "")), None)
        if server is None:
            return {"ok": False, "error": "That server is not in the popular servers list."}
        settings = self.config.get()
        number = str(body.get("versionNumber") or settings.get("defaultVersion") or "")
        loader = str(body.get("loader") or settings.get("defaultLoader") or "")
        if server.get("versions") and not popular.compatible(number, server["versions"]):
            rows = list_versions(str(settings["gameDirectory"]))
            installed = [str(v["versionNumber"]) for v in rows if v.get("supported")]
            picked = popular.choose_version(server["versions"], installed)
            if picked is None:
                from vanta.launcher.minecraft import instances as inst
                try:
                    available = [str(v.get("id") or v.get("versionNumber")) for v in inst.available_versions().get("versions") or []]
                except Exception:  # noqa: BLE001
                    available = []
                return {"ok": False, "launched": False, "needsInstall": popular.suggest_install(server["versions"], available),
                        "error": f"{server['name']} accepts {server['versions']}; no installed instance fits."}
            number = picked
            loader = next((str(v["loader"]) for v in rows if str(v["versionNumber"]) == picked and v.get("supported")), loader)
            body["versionNumber"], body["loader"] = number, loader
        version = get_version(number, loader, str(settings["gameDirectory"]))
        if version is None:
            return {"ok": False, "error": f"Version {number} {loader} is not installed."}
        instance = Path(str(version["gameDirectory"]))
        profile = self.profiles.get(str(body.get("profileName") or settings.get("selectedProfileName") or ""))
        linked = str((profile or {}).get("instanceDirectory") or "").strip()
        if linked and not version.get("downloaded"):
            instance = Path(linked)
        added = False
        try:
            added = popular.add_to_server_list(instance / "servers.dat", server["name"], popular.address(server))
        except (OSError, ValueError) as exc:
            self.log.error(f"Could not update servers.dat: {exc}")
        # No port: the Minecraft client resolves the SRV record from the hostname itself.
        body["joinServer"] = {"host": server["host"], "port": server.get("port")}
        body.setdefault("targetMode", "local")
        result = self.play(body)
        result["serverAdded"] = added
        result["server"] = {"name": server["name"], "address": popular.address(server)}
        return result

    def logs(self) -> dict[str, Any]:
        return {"lines": self.log.tail()}

    def last_error(self) -> str:
        return self.log.last_error

    def updates(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return updater.check_for_updates(force=str((payload or {}).get("force") or "") == "1")

    def apply_update(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        info = updater.check_for_updates()
        if not info.get("ok") or not info.get("available"):
            return {"ok": False, "error": info.get("message") or "No update available."}
        if self._game_running():
            return {"ok": False, "error": "Close Minecraft first, then update."}
        job = updater.start_update(repo_root(), str(info["latest"]))
        self.log.info(f"Updating Vanta to {info['latest']} (hash-checked files from GitHub).")
        return {"ok": True, "job": job}

    def update_job(self) -> dict[str, Any]:
        job = updater.update_job()
        if job.get("state") in {"done", "failed"} and not job.get("logged"):
            res = job.get("result") or {}
            self.log.info(f"Update to {job.get('tag')} {job.get('state')}: {len(res.get('updated') or [])} files replaced, "
                          f"{len(res.get('failed') or [])} failed. Restart Vanta to finish.")
            updater.mark_logged()
        return {"ok": True, "job": job}

    def trailer_progress(self) -> dict[str, Any]:
        from vanta.launcher.minecraft import instances
        return {"ok": True, "progress": instances.trailer_progress()}

    def first_run_status(self) -> dict[str, Any]:
        from vanta.launcher.minecraft import first_run
        return {"ok": True, "firstRun": first_run.status(self.data_dir)}

    def first_run_retry(self) -> dict[str, Any]:
        from vanta.launcher.minecraft import first_run
        if not first_run.needs_install(self.data_dir):
            return {"ok": True, "started": False, "message": "Minecraft is already installed."}
        started = first_run.maybe_start(self.data_dir)
        if started:
            self.log.info("First-run download restarted.")
        return {"ok": True, "started": started}

    # ---- game monitor ----
    def _game_running(self) -> bool:
        from vanta.launcher.minecraft import monitor
        return bool(monitor.game_status(self.data_dir, lines=1)["running"])

    def game_status(self) -> dict[str, Any]:
        from vanta.launcher.minecraft import monitor
        return monitor.game_status(self.data_dir)

    def stop_game(self) -> dict[str, Any]:
        from vanta.launcher.minecraft.launch import stop_client
        return stop_client(self.data_dir)

    def dismiss_crash(self) -> dict[str, Any]:
        from vanta.launcher.minecraft import monitor
        monitor.dismiss_crash()
        return {"ok": True}

    # ---- per-instance tools ----
    def _instance_dir(self, body: dict[str, Any]) -> tuple[Path | None, dict[str, Any] | None]:
        settings = self.config.get()
        version = get_version(str(body.get("versionNumber") or settings.get("defaultVersion") or ""),
                              str(body.get("loader") or settings.get("defaultLoader") or ""), str(settings["gameDirectory"]))
        if version is None:
            return None, None
        return Path(str(version["gameDirectory"])), version

    def instance_presets(self) -> dict[str, Any]:
        from vanta.launcher.minecraft import instances
        from vanta.shared import macplatform
        warn = macplatform.TRAILER_IGPU_WARNING if macplatform.intel_igpu_only() else ""
        return {"ok": True, "presets": [{"id": k, **v, "mods": [t for _, t in instances.preset_mod_list(k)],
                                         "warning": warn if k == "trailer" else ""} for k, v in instances.PRESETS.items()]}

    def instance_mod_search(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        version = str(body.get("version") or "").strip()
        query = str(body.get("query") or "").strip()
        if not version:
            return {"ok": False, "error": "Choose a Minecraft version first."}
        key = (version, query.lower())
        hit = self._mod_search_cache.get(key) if hasattr(self, "_mod_search_cache") else None
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        try:
            projects = self.mod_repository.search(query, game_version=version, loader="fabric", limit=24)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Modrinth search failed: {exc}"[:200]}
        result = {"ok": True, "version": version, "results": [
            {"slug": p.slug, "title": p.title, "description": p.description, "icon": p.icon_url, "downloads": p.download_count}
            for p in projects]}
        if not hasattr(self, "_mod_search_cache"):
            self._mod_search_cache = {}
        self._mod_search_cache[key] = (time.time(), result)
        return result

    def instance_trailer(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from vanta.launcher.minecraft import instances
        body = payload or {}
        folder, version = self._instance_dir(body)
        if folder is None or version is None:
            return {"ok": False, "error": "That instance is not installed."}
        if self._game_running():
            return {"ok": False, "error": "Close Minecraft first; mods can't change while it's running."}
        on = bool(body.get("on"))
        report = instances.track_trailer(folder.name)
        try:
            result = instances.set_trailer(folder, str(version["versionNumber"]), on, progress=report if on else None)
        except Exception as exc:  # noqa: BLE001
            instances.finish_trailer(False)
            return {"ok": False, "error": f"Trailer style change failed: {exc}"[:300]}
        instances.finish_trailer(bool(result.get("ok")))
        if result.get("ok"):
            self.log.info(f"Trailer style {'on' if on else 'off'} for {folder.name}.")
        return result

    def instance_info(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from vanta.launcher.minecraft import instances
        body = payload or {}
        folder, version = self._instance_dir(body)
        if folder is None or version is None:
            return {"ok": False, "error": "That instance is not installed."}
        meta = {}
        if (folder / instances.META_NAME).is_file():
            meta = json.loads((folder / instances.META_NAME).read_text(encoding="utf-8"))
        cfg = self._instance_settings(folder)
        mods = []
        if (folder / "mods").is_dir():
            for jar in sorted((folder / "mods").iterdir(), key=lambda p: p.name.lower()):
                if jar.name.endswith((".jar", ".jar.disabled")):
                    mods.append({"file": jar.name, "enabled": jar.name.endswith(".jar"), "sizeKb": jar.stat().st_size // 1024})
        shots = []
        if (folder / "screenshots").is_dir():
            for png in sorted((folder / "screenshots").glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:60]:
                shots.append({"name": png.name, "at": int(png.stat().st_mtime)})
        main = folder.name in instances.PROTECTED
        return {"ok": True, "id": folder.name, "folder": str(folder), "main": main, "downloaded": bool(version.get("downloaded")),
                "preset": meta.get("preset") or ("trailer" if main else ("vanilla" if folder.name.endswith("-vanilla") else "fast")),
                "trailer": True if main else bool(meta.get("trailer")), "skipped": meta.get("skippedMods") or [],
                "settings": cfg, "mods": mods, "modsEditable": not main, "screenshots": shots}

    def _instance_settings(self, folder: Path) -> dict[str, Any]:
        path = folder / "vanta-settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            data = {}
        return {"ramGb": int(data.get("ramGb") or 0), "jvmArgs": str(data.get("jvmArgs") or "")}

    def save_instance_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        folder, _ = self._instance_dir(body)
        if folder is None:
            return {"ok": False, "error": "That instance is not installed."}
        try:
            ram = int(body.get("ramGb") or 0)
        except (TypeError, ValueError):
            ram = 0
        if ram and not 1 <= ram <= 64:
            return {"ok": False, "error": "RAM must be between 1 and 64 GB (0 = use the PLAY slider)."}
        args = " ".join(str(body.get("jvmArgs") or "").split())[:600]
        bad = [a for a in args.split() if not a.startswith(("-X", "-XX:", "-D"))]
        if bad:
            return {"ok": False, "error": "JVM arguments must start with -X, -XX: or -D. Not allowed: " + " ".join(bad[:3])}
        (folder / "vanta-settings.json").write_text(json.dumps({"ramGb": ram, "jvmArgs": args}, indent=2), encoding="utf-8")
        return {"ok": True, "settings": {"ramGb": ram, "jvmArgs": args}}

    def toggle_instance_mod(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from vanta.launcher.minecraft import instances
        body = payload or {}
        folder, _ = self._instance_dir(body)
        name = str(body.get("file") or "")
        if folder is None or not name or "/" in name or "\\" in name or ".." in name:
            return {"ok": False, "error": "Unknown mod."}
        if folder.name in instances.PROTECTED:
            return {"ok": False, "error": "Use the MODS tab for the main 1.21.11 instance."}
        if self._game_running():
            return {"ok": False, "error": "Close Minecraft first."}
        jar = folder / "mods" / name
        if not jar.is_file():
            return {"ok": False, "error": "That mod file is gone."}
        target = jar.with_name(name[:-len(".disabled")]) if name.endswith(".disabled") else jar.with_name(name + ".disabled")
        jar.rename(target)
        return {"ok": True, "file": target.name, "enabled": target.name.endswith(".jar")}

    def screenshot_file(self, payload: dict[str, Any] | None = None) -> tuple[int, bytes, str]:
        body = payload or {}
        folder, _ = self._instance_dir(body)
        name = str(body.get("name") or "")
        if folder is None or not re.fullmatch(r"[\w .()-]+\.png", name):
            return 404, b"Not found", "text/plain"
        path = folder / "screenshots" / name
        return (200, path.read_bytes(), "image/png") if path.is_file() else (404, b"Not found", "text/plain")

    def open_folder(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        import subprocess
        import sys
        body = payload or {}
        folder, _ = self._instance_dir(body)
        if folder is None:
            return {"ok": False, "error": "That instance is not installed."}
        sub = str(body.get("sub") or "")
        if sub in {"screenshots", "mods", "resourcepacks", "shaderpacks", "saves", "logs", "crash-reports"}:
            folder = folder / sub
            folder.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"Could not open the folder: {exc}", "folder": str(folder)}
        return {"ok": True, "folder": str(folder)}

    # ---- Modrinth browser (v1.4.0) ----
    def _browse_ctx(self, body: dict[str, Any]) -> tuple[Path | None, str, str]:
        folder, version = self._instance_dir(body)
        if folder is None or version is None:
            return None, str(body.get("versionNumber") or ""), "fabric"
        loader = str(version.get("loader") or "fabric").lower()
        return folder, str(version["versionNumber"]), "fabric" if loader in {"vanilla", ""} else loader

    def browse_rows(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        folder, game, loader = self._browse_ctx(body)
        if not game:
            return {"ok": False, "error": "Choose an instance first."}
        rows = self.browser.rows(game, loader)
        installed = self.browser.installed(folder, game, loader) if folder else {}
        return {"ok": True, "gameVersion": game, "loader": loader, "rows": rows, "installed": installed,
                "instance": folder.name if folder else ""}

    def browse_search(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        _, game, loader = self._browse_ctx(body)
        try:
            items = self.browser.search(str(body.get("query") or ""), game_version=str(body.get("gameVersion") or game),
                                        loader=str(body.get("loader") or loader), project_type=str(body.get("type") or "mod"),
                                        category=str(body.get("category") or ""), index=str(body.get("sort") or "relevance"),
                                        limit=int(body.get("limit") or 30), offset=int(body.get("offset") or 0))
        except (BrowserError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "results": items}

    def browse_details(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        _, game, loader = self._browse_ctx(body)
        try:
            return {"ok": True, "project": self.browser.details(str(body.get("project") or ""), game, loader)}
        except BrowserError as exc:
            return {"ok": False, "error": str(exc)}

    def browse_categories(self) -> dict[str, Any]:
        try:
            return {"ok": True, "categories": self.browser.categories()}
        except BrowserError as exc:
            return {"ok": False, "error": str(exc), "categories": []}

    def browse_installed(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        folder, game, loader = self._browse_ctx(body)
        if folder is None:
            return {"ok": False, "error": "That instance is not installed."}
        return {"ok": True, "installed": self.browser.installed(folder, game, loader, check_updates=bool(body.get("updates")))}

    def browse_install(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        folder, game, loader = self._browse_ctx(body)
        if folder is None:
            return {"ok": False, "error": "That instance is not installed yet. Install it in INSTANCES first."}
        if self._game_running():
            return {"ok": False, "error": "Close Minecraft first; files can't change while it's running."}
        project = str(body.get("project") or "")
        if not re.fullmatch(r"[\w\-]{1,64}", project):
            return {"ok": False, "error": "Unknown project."}
        job = self.browser.start_install(folder, project, game, loader, str(body.get("versionId") or ""))
        self.log.info(f"Modrinth install {project} -> {folder.name}.")
        return {"ok": True, "job": job}

    def browse_job(self, job_id: str) -> dict[str, Any]:
        job = self.browser.jobs.get(job_id)
        return {"ok": True, **job} if job else {"ok": False, "error": "Unknown job."}

    def browse_remove(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        folder, game, loader = self._browse_ctx(body)
        if folder is None:
            return {"ok": False, "error": "That instance is not installed."}
        if self._game_running():
            return {"ok": False, "error": "Close Minecraft first."}
        return self.browser.remove(folder, str(body.get("projectId") or ""), game, loader)

    # ---- Friends (no server: local list, LAN beacons, e4all invites) ----
    def _friend_name(self) -> str:
        acc = self.accounts.get(self.config.get().get("selectedAccountId")) or next(iter(self.accounts.list()), None)
        return str((acc or {}).get("username") or "Player")

    def start_friends_lan(self) -> None:
        """LAN beacons run while Vanta is open, so friends nearby see you on any tab."""
        import threading
        self.friends.ensure_lan()

        def loop() -> None:
            while True:
                try:
                    self._update_friend_presence({})
                except Exception:  # noqa: BLE001 - presence is best effort
                    pass
                time.sleep(10)
        threading.Thread(target=loop, name="vanta-friends-presence", daemon=True).start()

    def friends_call(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from vanta.launcher.friends.codes import CodeError
        body = payload or {}
        f = self.friends
        try:
            if action in {"state", "overview"}:
                f.ensure_lan()
                self._update_friend_presence(body)
                return f.overview()
            if action == "add":
                return f.add(str(body.get("code") or ""))
            if action == "remove":
                return f.remove(str(body.get("id") or ""))
            if action == "invite":
                folder, version = self._instance_dir(body)
                if folder is None or version is None:
                    return {"ok": False, "error": "Pick the installed instance you are hosting from first."}
                return f.invite_for(folder, str(version["versionNumber"]), str(version.get("loader") or "fabric"),
                                    str(body.get("address") or ""))
            if action == "readinvite":
                return {"ok": True, "invite": f.read_invite(str(body.get("invite") or ""))}
            if action == "joininvite":
                inv = f.read_invite(str(body.get("invite") or ""))
                return self._join_address(inv["host"], inv["port"], inv["version"], inv["loader"], inv["hostName"])
            if action == "joinlan":
                t = f.lan_join_target(str(body.get("id") or ""))
                return self._join_address(t["host"], t["port"], t["version"], "fabric", t["name"])
        except CodeError as exc:
            return {"ok": False, "error": str(exc)}
        except (TypeError, ValueError):
            return {"ok": False, "error": "Bad request."}
        return {"ok": False, "error": "Unknown Friends action."}

    def _update_friend_presence(self, body: dict[str, Any]) -> None:
        from vanta.launcher.friends.service import find_lan_port, newest_world
        if not self._game_running():
            self.friends.set_presence(playing=False)
            return
        folder, version = self._instance_dir(body)
        mc = str((version or {}).get("versionNumber") or self.config.get().get("defaultVersion") or "")
        port = find_lan_port(folder) if folder else None
        self.friends.set_presence(playing=True, mc=mc, world=newest_world(folder) if folder and port else "", port=port)

    def _join_address(self, host: str, port: int | None, want: str, loader: str, who: str) -> dict[str, Any]:
        """PLAY straight into a world: use an installed instance of that version, or start installing one."""
        if self._game_running():
            return {"ok": False, "error": "Minecraft is already running. Close it first, then join."}
        settings = self.config.get()
        want = want or str(settings.get("defaultVersion") or "")
        rows = [v for v in list_versions(str(settings["gameDirectory"])) if v.get("supported") and str(v["versionNumber"]) == want]
        if not rows:
            started = self.install_instance({"version": want, "loader": loader or "fabric"})
            if not started.get("ok"):
                return {"ok": False, "error": f"{who or 'Your friend'} is on Minecraft {want}, and Vanta couldn't install it: {started.get('error')}"}
            return {"ok": True, "installing": True, "job": started.get("job") or started, "version": want,
                    "message": f"Installing Minecraft {want} to join {who or 'your friend'}. Vanta joins when it's ready."}
        row = next((v for v in rows if str(v.get("loader")) == (loader or "fabric")), None) or \
            next((v for v in rows if str(v.get("loader")) == "fabric"), rows[0])
        result = self.play({"versionNumber": want, "loader": row["loader"], "joinServer": {"host": host, "port": port},
                            "targetMode": "local"})
        result["joining"] = {"friend": who, "address": host + (f":{port}" if port else "")}
        return result

    def _selected_profile_name(self, payload: dict[str, Any] | None = None) -> str | None:
        body = payload or {}
        name = body.get("profileName") or body.get("profile") or self.config.get().get("selectedProfileName")
        if not name:
            return None
        profile = self.profiles.get(str(name))
        return profile["name"] if profile else None

    def search_mods(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        query = str(body.get("query") or body.get("q") or "")
        profile_name = self._selected_profile_name(body)
        if profile_name is None:
            message = "No profile selected. Choose a profile before browsing mods."
            self.log.error(message)
            return {"ok": False, "error": message}
        try:
            limit = int(body.get("limit") or 20)
            offset = int(body.get("offset") or 0)
            projects = self.mod_repository.search(
                query,
                game_version=self.mods.game_version,
                loader=self.mods.loader,
                limit=limit,
                offset=offset,
            )
            cards = [self.mods.card_for_project(profile_name, project) for project in projects]
            return {
                "ok": True,
                "query": query,
                "gameVersion": self.mods.game_version,
                "loader": self.mods.loader,
                "profileName": profile_name,
                "results": cards,
            }
        except ModRepositoryError as exc:
            self.log.error(exc.message)
            return {"ok": False, "error": exc.message}
        except Exception as exc:
            message = f"Mod search failed: {exc}"
            self.log.error(message)
            return {"ok": False, "error": message}

    def mod_details(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        project_id = str(body.get("projectId") or body.get("id") or body.get("slug") or "")
        profile_name = self._selected_profile_name(body)
        if profile_name is None:
            message = "No profile selected. Choose a profile before browsing mods."
            self.log.error(message)
            return {"ok": False, "error": message}
        if not project_id:
            return {"ok": False, "error": "A project id or slug is required."}
        try:
            project = self.mod_repository.get_project(project_id)
            versions = self.mod_repository.get_versions(
                project.id,
                game_version=self.mods.game_version,
                loader=self.mods.loader,
            )
            card = self.mods.card_for_project(profile_name, project, versions=versions)
            return {"ok": True, "profileName": profile_name, "mod": card}
        except ModRepositoryError as exc:
            self.log.error(exc.message)
            return {"ok": False, "error": exc.message}

    def list_installed_mods(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        profile_name = self._selected_profile_name(body)
        if profile_name is None:
            message = "No profile selected. Choose a profile before managing mods."
            self.log.error(message)
            return {"ok": False, "error": message}
        mods = self.mods.installed_cards(profile_name)
        return {
            "ok": True,
            "profileName": profile_name,
            "modsDir": str(self.mods.mods_dir(profile_name)),
            "mods": mods,
        }

    def install_mod(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        profile_name = self._selected_profile_name(body)
        if profile_name is None:
            message = "No profile selected. Choose a profile before installing mods."
            self.log.error(message)
            return {"ok": False, "error": message}
        result = self.mods.install(
            profile_name,
            project_id=str(body.get("projectId") or "") or None,
            version_id=str(body.get("versionId") or "") or None,
            auto_deps=bool(body.get("autoDeps", True)),
        )
        if result.get("ok"):
            title = (result.get("mod") or {}).get("title") or "mod"
            self.log.info(f"Installed '{title}' on profile '{profile_name}'.")
        else:
            self.log.error(str(result.get("error") or "Mod install failed."))
        return result

    def remove_mod(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        profile_name = self._selected_profile_name(body)
        if profile_name is None:
            message = "No profile selected. Choose a profile before removing mods."
            self.log.error(message)
            return {"ok": False, "error": message}
        project_id = str(body.get("projectId") or "")
        if not project_id:
            return {"ok": False, "error": "A project id is required."}
        result = self.mods.remove(profile_name, project_id)
        if result.get("ok"):
            self.log.info(f"Removed mod {project_id} from profile '{profile_name}'.")
        else:
            self.log.error(str(result.get("error") or "Mod remove failed."))
        return result

    def set_mod_enabled(self, payload: dict[str, Any] | None = None, *, enabled: bool) -> dict[str, Any]:
        body = payload or {}
        profile_name = self._selected_profile_name(body)
        if profile_name is None:
            message = "No profile selected. Choose a profile before changing mods."
            self.log.error(message)
            return {"ok": False, "error": message}
        project_id = str(body.get("projectId") or "")
        if not project_id:
            return {"ok": False, "error": "A project id is required."}
        result = self.mods.set_enabled(profile_name, project_id, enabled)
        if result.get("ok"):
            state = "enabled" if enabled else "disabled"
            self.log.info(f"Mod {project_id} {state} on profile '{profile_name}'.")
        else:
            self.log.error(str(result.get("error") or "Could not change mod state."))
        return result

    def _selected_local_account(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        body = payload or {}
        account_id = body.get("accountId") or self.config.get().get("selectedAccountId")
        account = self.accounts.get(str(account_id)) if account_id else None
        if account is not None:
            return account
        username = str(body.get("username") or "").strip()
        if not username:
            return None
        for item in self.accounts.list():
            if item.get("username") == username and item.get("mode") == "local":
                return item
        return None

    def skins_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        state = self.skins.state()
        account = self._selected_local_account(payload)
        applied = None
        if account and account.get("skinFile"):
            applied = {
                "accountId": account.get("id"),
                "username": account.get("username"),
                "label": account.get("label"),
                "skinId": account.get("skinId"),
                "model": account.get("skinModel"),
                "skinFile": account.get("skinFile"),
                "uploadedToMicrosoft": False,
                "note": account.get("skinNote") or LOCAL_APPLY_NOTE,
            }
        state["applied"] = applied
        return state

    def search_skins(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED}
        query = str(body.get("query") or body.get("q") or "")
        skin_filter = str(body.get("filter") or "all")
        try:
            limit = int(body.get("limit") or 5)
        except (TypeError, ValueError):
            limit = 5
        result = self.skins.search(query, skin_filter=skin_filter, limit=limit)
        if not result.get("ok"):
            self.log.error(str(result.get("error") or "Skin search failed."))
        return result

    def favorite_skin(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        skin_id = str(body.get("skinId") or body.get("id") or "")
        if not skin_id:
            return {"ok": False, "error": "A skin id is required."}
        favorite = body.get("favorite")
        if favorite is None:
            favorite = True
        return self.skins.set_favorite(skin_id, bool(favorite))

    def view_skin(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        skin_id = str(body.get("skinId") or body.get("id") or "")
        if not skin_id:
            return {"ok": False, "error": "A skin id is required."}
        return self.skins.view(skin_id)

    def set_skin_model(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED}
        skin_id = str(body.get("skinId") or body.get("id") or "")
        model = str(body.get("model") or "")
        if not skin_id:
            return {"ok": False, "error": "A skin id is required."}
        result = self.skins.set_model(skin_id, model)
        if not result.get("ok"):
            return result
        account = self._selected_local_account(body)
        if account and account.get("skinId") == skin_id and account.get("skinFile"):
            try:
                saved = self.accounts.save_local_skin(
                    account["id"],
                    skin_id=skin_id,
                    model=result["skin"]["model"],
                    relative_file=str(account["skinFile"]),
                    source=str(account.get("skinSource") or result["skin"].get("source") or ""),
                    note=str(account.get("skinNote") or LOCAL_APPLY_NOTE),
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            result["account"] = saved
        return result

    def import_skin(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        import base64

        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED}
        raw = body.get("pngBase64") or body.get("png") or ""
        if not isinstance(raw, str) or not raw.strip():
            return {"ok": False, "error": "A PNG skin is required."}
        encoded = raw.strip()
        if "," in encoded and encoded.lower().startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            png = base64.b64decode(encoded, validate=True)
        except Exception:
            return {"ok": False, "error": "That skin payload is not valid base64."}
        result = self.skins.import_png(
            png,
            name=str(body.get("name") or body.get("filename") or "Imported skin"),
            model=str(body.get("model") or "steve"),
        )
        if result.get("ok"):
            self.log.info(f"Imported local skin '{result['skin']['name']}'. Not uploaded to Microsoft.")
        else:
            self.log.error(str(result.get("error") or "Skin import failed."))
        return result

    def apply_skin(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED}
        account = self._selected_local_account(body)
        if account is None:
            message = "No local account selected. Create a LOCAL TEST ACCOUNT before saving a skin."
            self.log.error(message)
            return {"ok": False, "error": message}
        skin_id = str(body.get("skinId") or body.get("id") or "")
        if not skin_id:
            return {"ok": False, "error": "A skin id is required."}
        model = str(body.get("model") or "") or None
        result = self.skins.apply_local(account, skin_id, model)
        if not result.get("ok"):
            self.log.error(str(result.get("error") or "Could not save that skin."))
            return result
        try:
            saved = self.accounts.save_local_skin(
                account["id"],
                skin_id=skin_id,
                model=str(result.get("model") or "steve"),
                relative_file=str(result["skinFile"]),
                source=str((result.get("skin") or {}).get("source") or ""),
                note=LOCAL_APPLY_NOTE,
            )
        except ValueError as exc:
            self.log.error(str(exc))
            return {"ok": False, "error": str(exc)}
        self.log.info(
            f"Saved skin for LOCAL TEST ACCOUNT '{saved['username']}' locally. "
            "Not uploaded to Microsoft."
        )
        result["account"] = saved
        result["uploadedToMicrosoft"] = False
        result["note"] = LOCAL_APPLY_NOTE
        return result

    def skin_texture(self, skin_id: str) -> tuple[int, bytes, str]:
        loaded = self.skins.texture(skin_id)
        if isinstance(loaded, dict):
            message = str(loaded.get("error") or "Could not load that skin.")
            self.log.error(message)
            body = (message + "\n").encode("utf-8")
            return 400, body, "text/plain; charset=utf-8"
        png, _record = loaded
        return 200, png, "image/png"

    def list_managed_servers(self) -> dict[str, Any]:
        return self.servers.list_servers()

    def create_server(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED, "created": False}
        result = self.servers.create(body)
        if result.get("ok"):
            server = result.get("server") or {}
            jar = result.get("jar")
            if jar is None:
                note = "No server jar was downloaded."
            elif jar.get("ok"):
                note = f"Installed {jar['jar'].get('name')} as server.jar."
            else:
                note = f"Server jar was not installed: {jar.get('error')}"
            self.log.info(
                f"Created local {server.get('software')} server '{server.get('name')}' "
                f"on port {server.get('port')}. {note}"
            )
        else:
            self.log.error(str(result.get("error") or "Could not create a server."))
        return result

    def install_server_jar(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.servers.install_jar(self._server_id(payload))
        if result.get("ok"):
            self.log.info(f"Installed server jar {(result.get('jar') or {}).get('name')}.")
        else:
            self.log.error(str(result.get("error") or "Could not install the server jar."))
        return result

    def start_server(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.servers.start(self._server_id(payload))
        if result.get("ok"):
            self.log.info("Started a local server process. It is separate from the launcher.")
        else:
            self.log.error(str(result.get("error") or "Could not start that server."))
        return result

    def stop_server(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.servers.stop(self._server_id(payload))
        if result.get("ok"):
            self.log.info("Stopped local server process.")
        else:
            self.log.error(str(result.get("error") or "Could not stop that server."))
        return result

    def restart_server(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.servers.restart(self._server_id(payload))
        if not result.get("ok"):
            self.log.error(str(result.get("error") or "Could not restart that server."))
        else:
            self.log.info("Restarted local server process.")
        return result

    def server_console(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.servers.console(self._server_id(payload))

    def update_server_settings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED}
        result = self.servers.update_settings(self._server_id(body), body)
        if result.get("ok"):
            self.log.info("Stored server port, RAM, and JVM arguments. JVM arguments were not executed.")
        else:
            self.log.error(str(result.get("error") or "Could not store server settings."))
        return result

    def server_properties(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.servers.properties(self._server_id(payload))

    def update_server_properties(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED}
        result = self.servers.update_properties(self._server_id(body), body)
        if result.get("ok"):
            self.log.info("Wrote server.properties. A running server process is not told to reload it.")
        else:
            self.log.error(str(result.get("error") or "Could not write server.properties."))
        return result

    def backup_server(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self.servers.backup(self._server_id(payload))
        if result.get("ok"):
            self.log.info(f"Wrote server backup {result.get('name')}.")
        else:
            self.log.error(str(result.get("error") or "Could not back up that server."))
        return result

    def server_players(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.servers.players(self._server_id(payload))

    def list_server_plugins(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.servers.list_plugins(self._server_id(payload))

    def install_server_plugin(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED}
        source = str(body.get("sourcePath") or body.get("path") or "")
        result = self.servers.install_plugin(self._server_id(body), source)
        if result.get("ok"):
            self.log.info("Copied a local plugin jar into plugins/.")
        else:
            self.log.error(str(result.get("error") or "Could not install that plugin."))
        return result

    def set_server_plugin_enabled(self, payload: dict[str, Any] | None, *, enabled: bool) -> dict[str, Any]:
        body = payload or {}
        name = str(body.get("name") or body.get("file") or "")
        result = self.servers.set_plugin_enabled(self._server_id(body), name, enabled)
        if not result.get("ok"):
            self.log.error(str(result.get("error") or "Could not change that plugin."))
        return result

    def remove_server_plugin(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        name = str(body.get("name") or body.get("file") or "")
        result = self.servers.remove_plugin(self._server_id(body), name)
        if not result.get("ok"):
            self.log.error(str(result.get("error") or "Could not remove that plugin."))
        return result

    def set_server_tunnel(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            return {"ok": False, "error": TOKEN_REJECTED, "status": "OFFLINE", "address": None, "started": False}
        result = self.servers.set_tunnel(self._server_id(body), body)
        if result.get("ok") and result.get("status") == "ONLINE":
            self.log.info("Tunnel is online for a Vanta server. The address came from the local tunnel tool.")
        elif result.get("enabled") is False and result.get("ok"):
            self.log.info("Tunnel disabled. No tunnel process was left running.")
        elif not result.get("ok"):
            self.log.error(str(result.get("error") or "Could not change that tunnel."))
        return result

    def server_tunnel(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.servers.tunnel_status(self._server_id(payload))

    def _server_id(self, payload: dict[str, Any] | None) -> str:
        body = payload or {}
        return str(body.get("serverId") or body.get("id") or "")

    def _named_profile(self, payload: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
        body = payload or {}
        if contains_token_key(body):
            return None, TOKEN_REJECTED
        raw_name = body.get("profileName") if "profileName" in body else body.get("profile")
        name = str(raw_name).strip() if isinstance(raw_name, str) else ""
        if not name:
            selected = self.config.get().get("selectedProfileName")
            name = str(selected).strip() if isinstance(selected, str) else ""
        if not name:
            return None, "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
        profile = self.profiles.get(name)
        if profile is None:
            return None, f"No profile named {name}."
        return profile, None

    def _hud(self, payload: dict[str, Any] | None, method: Any) -> dict[str, Any]:
        profile, error = self._named_profile(payload)
        if error or profile is None:
            message = error or "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
            self.log.error(message)
            return {"ok": False, "error": message}
        try:
            state = method(profile["name"], payload or {})
        except ValueError as exc:
            self.log.error(str(exc))
            return {"ok": False, "error": str(exc)}
        state["ok"] = True
        return state

    def hud_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._hud(payload, lambda name, _body: self.client_ui.public_state(name))

    def update_hud_module(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._hud(payload, self.client_ui.update_module)
        if result.get("ok"):
            self.log.info(
                f"Saved HUD module layout for '{result.get('profileName')}'. It was not shown in Minecraft."
            )
        return result

    def reset_hud(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._hud(payload, self.client_ui.reset_layout)
        if result.get("ok"):
            self.log.info(
                f"Reset HUD layout for '{result.get('profileName')}'. It was not shown in Minecraft."
            )
        return result

    def update_hud_editor(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._hud(payload, self.client_ui.update_editor)
        if result.get("ok"):
            self.log.info(f"Saved HUD editor state for '{result.get('profileName')}'.")
        return result

    def apply_hud_preset(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._hud(payload, self.client_ui.apply_preset)
        if result.get("ok"):
            preset = (result.get("performance") or {}).get("preset")
            self.log.info(
                f"Stored performance preset {preset} for '{result.get('profileName')}'. "
                "No frame rate was measured and no mods were downloaded."
            )
        return result

    def update_hud_visual(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._hud(payload, self.client_ui.update_visual)
        if result.get("ok"):
            self.log.info(
                f"Stored visual settings for '{result.get('profileName')}'. They were not applied in a game."
            )
        return result

    def update_hud_qol(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        result = self._hud(payload, self.client_ui.update_qol)
        if result.get("ok"):
            self.log.info(
                f"Stored quality-of-life settings for '{result.get('profileName')}'. They were not applied in a game."
            )
        return result


    def appearance_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        profile, error = self._named_profile(payload)
        if error or profile is None:
            message = error or "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
            self.log.error(message)
            return {"ok": False, "error": message, "shaderRunning": False, "testedInMinecraft": False, "minecraftLaunched": False}
        return self.appearance.state(profile["name"])

    def set_resource_pack(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._appearance(payload, self.appearance.select_pack, "resource pack")

    def set_shader_preset(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._appearance(payload, self.appearance.select_shader, "shader preset")

    def _appearance(self, payload: dict[str, Any] | None, method: Any, label: str) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            self.log.error(TOKEN_REJECTED)
            return {
                "ok": False,
                "error": TOKEN_REJECTED,
                "shaderRunning": False,
                "testedInMinecraft": False,
                "minecraftLaunched": False,
            }
        profile, error = self._named_profile(body)
        if error or profile is None:
            message = error or "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
            self.log.error(message)
            return {"ok": False, "error": message, "shaderRunning": False, "testedInMinecraft": False, "minecraftLaunched": False}
        try:
            result = method(profile["name"], body)
        except ValueError as exc:
            self.log.error(str(exc))
            return {
                "ok": False,
                "error": str(exc),
                "shaderRunning": False,
                "testedInMinecraft": False,
                "packComplete": False,
                "minecraftLaunched": False,
            }
        if label == "shader preset" and result.get("shader", {}).get("status") == "NOT_APPLIED":
            self.log.info(
                f"Stored shader preset '{result.get('shader', {}).get('name')}' for '{profile['name']}'. "
                f"{result.get('warning')} Minecraft was not launched."
            )
        elif label == "shader preset":
            self.log.info(f"Shader preset is off for '{profile['name']}'. No shader is running.")
        elif result.get("resourcePack", {}).get("status") == "SELECTED":
            self.log.info(
                f"Copied resource pack '{result.get('resourcePack', {}).get('name')}' into profile "
                f"'{profile['name']}'. It was not tested in Minecraft."
            )
        else:
            self.log.info(f"Cleared the resource pack selection for '{profile['name']}'.")
        return result

    def _controller_error(self, message: str, profile_name: str | None = None) -> dict[str, Any]:
        detection = detect_controllers()
        body = {
            "ok": False,
            "error": message,
            "status": detection["status"],
            "deviceFound": detection["deviceFound"],
            "devices": detection["devices"],
            "checks": detection["checks"],
            "checkSummary": detection["checkSummary"],
            "testedInMinecraft": False,
            "inGameControllerPlayTested": False,
            "minecraftLaunched": False,
            "labelsOnly": True,
            "appliedInGame": False,
        }
        if profile_name:
            body["profileName"] = profile_name
        return body

    def controller_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        profile, error = self._named_profile(payload)
        if error or profile is None:
            message = error or "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
            self.log.error(message)
            return self._controller_error(message)
        return self.controller.state(profile["name"])

    def update_controller(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            self.log.error(TOKEN_REJECTED)
            return self._controller_error(TOKEN_REJECTED)
        profile, error = self._named_profile(body)
        if error or profile is None:
            message = error or "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
            self.log.error(message)
            return self._controller_error(message)
        try:
            result = self.controller.update(profile["name"], body)
        except ValueError as exc:
            self.log.error(str(exc))
            return self._controller_error(str(exc), profile["name"])
        self.log.info(
            f"Saved controller labels for '{profile['name']}'. "
            "They were not sent to Minecraft. In-game controller play was not tested."
        )
        return result

    def utilities_state(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        profile, error = self._named_profile(payload)
        if error or profile is None:
            message = error or "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
            self.log.error(message)
            return {"ok": False, "error": message, "worksInGame": False, "minecraftLaunched": False}
        return self.utilities.state(profile["name"])

    def set_utility(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        if contains_token_key(body):
            self.log.error(TOKEN_REJECTED)
            return {
                "ok": False,
                "error": TOKEN_REJECTED,
                "downloaded": False,
                "jarWritten": False,
                "worksInGame": False,
                "minecraftLaunched": False,
            }
        profile, error = self._named_profile(body)
        if error or profile is None:
            message = error or "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
            self.log.error(message)
            return {"ok": False, "error": message, "worksInGame": False, "minecraftLaunched": False}
        try:
            result = self.utilities.set_enabled(profile["name"], body)
        except ValueError as exc:
            self.log.error(str(exc))
            return {
                "ok": False,
                "error": str(exc),
                "downloaded": False,
                "jarWritten": False,
                "worksInGame": False,
                "minecraftLaunched": False,
            }
        title = ""
        changed = result.get("changed")
        for item in result.get("utilities") or []:
            if item.get("id") == changed:
                title = str(item.get("title") or changed)
                break
        if result.get("ok") and any(item.get("id") == changed and item.get("status") == "ENABLED" for item in result.get("utilities") or []):
            self.log.info(
                f"Saved utility '{title}' as ENABLED for '{profile['name']}' after a Modrinth version listed "
                "Minecraft 1.21.11 and Fabric. No jar was vendored. Minecraft was not launched."
            )
        elif result.get("ok"):
            self.log.info(f"Saved utility '{title}' as OFF for '{profile['name']}'.")
        else:
            self.log.error(str(result.get("error") or "Could not change that utility."))
        return result



_RETIRED_SCENES = frozenset({"overworld", "nether", "end", "shift"})
_DEFAULT_BACKGROUND_URL = "/assets/backgrounds/default.png"
_BACKGROUND_MAX_BYTES = 8 * 1024 * 1024
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _image_bytes(data: bytes, suffix: str) -> bool:
    if suffix == ".png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return data.startswith(b"\xff\xd8\xff")
    if suffix == ".gif":
        return data.startswith(b"GIF87a") or data.startswith(b"GIF89a")
    if suffix == ".webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if suffix == ".bmp":
        return data.startswith(b"BM")
    return False
