"""Application services behind the local UI."""

from __future__ import annotations

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
from vanta.launcher.mods.repository import ModRepository, ModRepositoryError
from vanta.launcher.mods.utilities import UtilityManager
from vanta.launcher.skins.builtin import VantaSkinLibrary
from vanta.launcher.skins.manager import SkinManager
from vanta.launcher.skins.models import LOCAL_APPLY_NOTE
from vanta.launcher.skins.repository import SkinRepository
from vanta.launcher.servers.manager import ServerManager
from vanta.launcher.updates.stub import check_for_updates
from vanta import __version__ as VANTA_VERSION
from vanta.shared.config.paths import default_data_dir
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
        self.utilities = UtilityManager(self.data_dir, self.mod_repository)
        self.skin_repository = skin_repository or VantaSkinLibrary()
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
            "updates": "not implemented in 0.1",
            "dataDir": str(self.data_dir),
            "defaultVersion": settings["defaultVersion"],
            "defaultLoader": settings["defaultLoader"],
            "modsRepository": self.mod_repository.describe(),
            "skinsRepository": self.skin_repository.describe(),
        }

    def versions(self) -> dict[str, Any]:
        settings = self.config.get()
        items = list_versions(str(settings["gameDirectory"]))
        return {"versions": items}

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
        return attempt_play(
            config=self.config,
            accounts=self.accounts,
            profiles=self.profiles,
            log=self.log,
            data_dir=self.data_dir,
            payload=payload,
        )

    def logs(self) -> dict[str, Any]:
        return {"lines": self.log.tail()}

    def last_error(self) -> str:
        return self.log.last_error

    def updates(self) -> dict[str, Any]:
        return check_for_updates()

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
