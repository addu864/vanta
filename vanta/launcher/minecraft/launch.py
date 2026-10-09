"""Launch a local client, or say exactly why it did not start.

A LOCAL TEST ACCOUNT is never sent to an online-mode server. Vanta does not
invent a Microsoft token. The vanilla/Fabric argument template requires an
``--accessToken`` value; the process is given the offline placeholder ``0``,
which is not stored on the account and is not a Microsoft session.

``targetMode`` ``local-dry-run`` still writes the 0.1 plan and starts nothing.
Any other local target starts Java only when ``java`` is on PATH and
``client.jar`` is already in the instance directory.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from vanta.launcher.accounts.store import LOCAL_LABEL, AccountStore
from vanta.launcher.minecraft.versions import get_version
from vanta.launcher.settings.profiles import ProfileStore
from vanta.shared.config.store import ConfigStore
from vanta.shared.logging.log import VantaLog
from vanta.shared.utilities.guard import contains_token_key
from vanta.shared.utilities.jsonio import read_json, write_json
from vanta.shared.utilities.ram import parse_ram

ONLINE_TARGETS = {"online", "online-mode", "online_mode", "multiplayer-online"}
DRY_RUN_TARGETS = {"local-dry-run", "dry-run", "dry_run"}
NO_ACCOUNT = "No local account selected. Create a LOCAL TEST ACCOUNT before play."
TOKEN_REJECTED = "Vanta 0.1 does not accept or store tokens."
JAVA_MISSING = "Java was not found on PATH. Minecraft was not started."
CLIENT_JAR_NAME = "client.jar"
SESSION_NAME = "client-session.json"
# Required by the Mojang client argument template. Not a Microsoft token.
OFFLINE_ACCESS_TOKEN = "0"
PID_WAIT_SECONDS = 0.6


def detect_java() -> dict[str, Any]:
    executable = shutil.which("java")
    if not executable:
        return {
            "present": False,
            "executable": None,
            "message": JAVA_MISSING,
        }
    return {
        "present": True,
        "executable": executable,
        "message": (
            "Java is present on PATH, but Vanta 0.1 did not start Minecraft "
            "and did not download any game files."
        ),
    }


def client_jar_path(game_directory: str) -> Path:
    return Path(game_directory) / CLIENT_JAR_NAME


def jar_missing_message(path: Path) -> str:
    return f"Minecraft client jar is missing: {path}. Minecraft was not started."


def attempt_play(
    *,
    config: ConfigStore,
    accounts: AccountStore,
    profiles: ProfileStore,
    log: VantaLog,
    data_dir: Path,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    body = payload or {}
    if contains_token_key(body):
        return _fail(log, data_dir, [TOKEN_REJECTED])

    settings = config.get()
    errors: list[str] = []
    target = str(body.get("targetMode") or "local").strip().lower()

    account_id = body.get("accountId") or settings.get("selectedAccountId")
    account = accounts.get(account_id) if account_id else None
    if account is None:
        errors.append(NO_ACCOUNT)
    else:
        if (
            account.get("label") != LOCAL_LABEL
            or account.get("mode") != "local"
            or account.get("onlineModeValid") is not False
        ):
            errors.append(
                "The selected account is not a LOCAL TEST ACCOUNT. "
                "Vanta 0.1 will not use it as an online-mode login."
            )
        if target in ONLINE_TARGETS:
            errors.append(
                "LOCAL TEST ACCOUNT cannot be used for online-mode servers. "
                "Launching against an online-mode target is out of scope."
            )

    version_number = str(body.get("versionNumber") or settings.get("defaultVersion") or "")
    loader = str(body.get("loader") or settings.get("defaultLoader") or "")
    version = get_version(version_number, loader, str(settings.get("gameDirectory")))
    if not version_number:
        errors.append("No Minecraft version selected.")
    elif version is None:
        errors.append(
            f"Version {version_number} with loader {loader} is not in the Vanta registry."
        )
    elif version.get("supported") is not True:
        errors.append(
            f"Version {version['versionNumber']} with {version['loader']} is not fully supported "
            "in Vanta 0.1. The only fully supported version is 1.21.11 with Fabric."
        )
    if loader != "fabric":
        errors.append(f"Loader {loader or '(none)'} is not supported in Vanta 0.1. Use Fabric.")

    profile_name = body.get("profileName") or settings.get("selectedProfileName")
    profile = profiles.get(profile_name) if profile_name else None
    if profile is None:
        errors.append(
            "No profile selected. Choose Vanta Performance, Vanta Vanilla+, or create a profile."
        )

    if "ramGb" in body and body.get("ramGb") is not None:
        ram_raw = body.get("ramGb")
    elif profile is not None:
        ram_raw = profile.get("ramGb")
    else:
        ram_raw = settings.get("defaultRamGb")
    ram_ok, ram_value, ram_error = parse_ram(ram_raw)
    if not ram_ok:
        errors.append(ram_error)

    if errors or account is None or version is None or profile is None or ram_value is None:
        return _fail(log, data_dir, errors or ["Play could not be validated."])

    config.update({
        "selectedAccountId": account["id"],
        "selectedProfileName": profile["name"],
    })
    saved_profile = profiles.set_ram(profile["name"], ram_value)
    version, instance_error = _version_for_profile(version, saved_profile)
    if instance_error:
        return _fail(log, data_dir, [instance_error])

    if target in DRY_RUN_TARGETS:
        return _dry_run(
            config=config,
            log=log,
            data_dir=data_dir,
            account=account,
            version=version,
            saved_profile=saved_profile,
            ram_value=ram_value,
        )

    return _launch_local(
        config=config,
        log=log,
        data_dir=data_dir,
        account=account,
        version=version,
        saved_profile=saved_profile,
        ram_value=ram_value,
    )


def stop_client(data_dir: Path) -> dict[str, Any]:
    """Stop the Java process this launcher recorded, if it is still ours."""
    data_dir = Path(data_dir)
    session = _read_session(data_dir)
    pid = int(session.get("pid") or 0)
    jar = str(session.get("jar") or "")
    if pid <= 1 or not _pid_alive(pid):
        _clear_session(data_dir)
        return {"ok": True, "stopped": True, "running": False, "pid": None}
    if not _cmdline_matches(pid, jar):
        return {
            "ok": False,
            "stopped": False,
            "running": True,
            "pid": pid,
            "error": "The recorded pid is alive but it is not the Vanta client process. It was not killed.",
        }
    _signal_stop(pid)
    alive = _pid_alive(pid) and _cmdline_matches(pid, jar)
    if alive:
        return {
            "ok": False,
            "stopped": False,
            "running": True,
            "pid": pid,
            "error": "The Java process is still alive.",
        }
    _clear_session(data_dir)
    return {"ok": True, "stopped": True, "running": False, "pid": pid}


def _dry_run(
    *,
    config: ConfigStore,
    log: VantaLog,
    data_dir: Path,
    account: dict[str, Any],
    version: dict[str, Any],
    saved_profile: dict[str, Any],
    ram_value: int,
) -> dict[str, Any]:
    java = detect_java()
    plan = {
        "milestone": "0.1",
        "status": "Ready",
        "launched": False,
        "gameStarted": False,
        "dryRun": True,
        "reason": "Dry-run only. Vanta 0.1 does not download Minecraft or start the game.",
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "account": _public_account(account),
        "version": version,
        "profile": _public_profile(saved_profile),
        "ramGb": ram_value,
        "jvmArgsStoredNotExecuted": config.get().get("jvmArgs", ""),
        "java": java,
        "downloads": [],
    }
    plan_path = data_dir / "launch-plan.json"
    write_json(plan_path, plan)
    log.info(
        "Ready: dry-run launch plan for LOCAL TEST ACCOUNT "
        f"'{account['username']}'. Version {version['versionNumber']} {version['loader']}, "
        f"profile {saved_profile['name']}, RAM {ram_value} GB. Game was not started. {java['message']}"
    )
    return {
        "status": "Ready",
        "launched": False,
        "gameStarted": False,
        "dryRun": True,
        "errors": [],
        "error": "",
        "planPath": str(plan_path),
        "plan": plan,
    }


def _launch_local(
    *,
    config: ConfigStore,
    log: VantaLog,
    data_dir: Path,
    account: dict[str, Any],
    version: dict[str, Any],
    saved_profile: dict[str, Any],
    ram_value: int,
) -> dict[str, Any]:
    java = shutil.which("java")
    jar = client_jar_path(str(version["gameDirectory"]))
    missing: list[str] = []
    if not java:
        missing.append(JAVA_MISSING)
    if not jar.is_file() or jar.stat().st_size <= 0:
        missing.append(jar_missing_message(jar))
    if missing:
        log.error(" ".join(missing))
        return _fail(log, data_dir, missing)

    assert java is not None
    existing = _read_session(data_dir)
    existing_pid = int(existing.get("pid") or 0)
    if existing_pid and _pid_alive(existing_pid) and _cmdline_matches(existing_pid, str(existing.get("jar") or jar)):
        return _running_result(
            log=log,
            data_dir=data_dir,
            account=account,
            version=version,
            saved_profile=saved_profile,
            ram_value=ram_value,
            java=java,
            jar=jar,
            pid=existing_pid,
            command=list(existing.get("command") or []),
            minecraft_client=bool(existing.get("minecraftClient")),
            already=True,
        )

    command, minecraft_client = _build_command(
        java=java,
        jar=jar,
        instance=jar.parent,
        ram_gb=ram_value,
        account=account,
        version=version,
    )
    log_path = data_dir / "client.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    handle.write(
        f"\n--- local start {datetime.now().astimezone().isoformat(timespec='seconds')} ---\n"
    )
    _sync_profile_mods(str(saved_profile.get("name") or ""), jar.parent, data_dir)
    popen_kwargs: dict[str, Any] = {
        "cwd": str(jar.parent),
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        # start_new_session calls setsid and raises ValueError on Windows.
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except FileNotFoundError:
        handle.close()
        return _fail(log, data_dir, [JAVA_MISSING])
    except OSError as exc:
        handle.write(f"The Java process was not started: {exc}\n")
        handle.close()
        return _fail(log, data_dir, [f"The Java process was not started: {exc}"])
    handle.close()

    _write_session(
        data_dir,
        {
            "pid": process.pid,
            "jar": str(jar),
            "command": command,
            "minecraftClient": minecraft_client,
        },
    )
    deadline = time.time() + PID_WAIT_SECONDS
    while time.time() < deadline and process.poll() is None:
        time.sleep(0.05)
    alive = process.poll() is None and _pid_alive(process.pid) and _cmdline_matches(process.pid, str(jar))
    if not alive:
        code = process.poll()
        if code is None:
            _signal_stop(process.pid)
        _clear_session(data_dir)
        tail = _log_tail(log_path)
        detail = f"The Java process exited before it stayed alive (exit {code})."
        if tail:
            detail = f"{detail} {tail}"
        log.error(detail)
        return _fail(log, data_dir, [detail])

    return _running_result(
        log=log,
        data_dir=data_dir,
        account=account,
        version=version,
        saved_profile=saved_profile,
        ram_value=ram_value,
        java=java,
        jar=jar,
        pid=process.pid,
        command=command,
        minecraft_client=minecraft_client,
        already=False,
    )


def _build_command(
    *,
    java: str,
    jar: Path,
    instance: Path,
    ram_gb: int,
    account: dict[str, Any],
    version: dict[str, Any],
) -> tuple[list[str], bool]:
    """Return (argv, minecraft_client).

    A bare client.jar (the test stub, or a jar with no Mojang metadata) is
    started with ``java -jar``. Official metadata switches to the Fabric or
    vanilla classpath. Stored settings JVM arguments are not appended.
    """
    # The marker stays at the front of argv so /proc/pid/cmdline still shows the jar
    # when a long classpath is truncated.
    memory = [java, f"-Dvanta.clientJar={jar}", f"-Xms{ram_gb}G", f"-Xmx{ram_gb}G"]
    vanilla_path = instance / f"{version['versionNumber']}.json"
    fabric_path = instance / "fabric-profile.json"
    if not vanilla_path.is_file():
        return [*memory, "-jar", str(jar)], False

    from vanta.launcher.minecraft.install import build_official_command

    command = build_official_command(
        java=java,
        memory=memory[1:],
        instance=instance,
        client_jar=jar,
        vanilla_path=vanilla_path,
        fabric_path=fabric_path if fabric_path.is_file() else None,
        account=account,
        version_number=str(version["versionNumber"]),
        ram_gb=ram_gb,
    )
    return command, True


def _running_result(
    *,
    log: VantaLog,
    data_dir: Path,
    account: dict[str, Any],
    version: dict[str, Any],
    saved_profile: dict[str, Any],
    ram_value: int,
    java: str,
    jar: Path,
    pid: int,
    command: list[str],
    minecraft_client: bool,
    already: bool,
) -> dict[str, Any]:
    if not (_pid_alive(pid) and _cmdline_matches(pid, str(jar))):
        _clear_session(data_dir)
        return _fail(log, data_dir, ["The Java process is not alive. launched stays false."])
    game_started = bool(minecraft_client)
    reason = (
        "Minecraft Java process is alive."
        if game_started
        else "A local Java process is alive. This jar is not the official Minecraft client."
    )
    if already:
        reason = "Already running. " + reason
    plan = {
        "milestone": "1.0",
        "status": "Started",
        "launched": True,
        "gameStarted": game_started,
        "minecraftClient": game_started,
        "dryRun": False,
        "reason": reason,
        "createdAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pid": pid,
        "command": command,
        "account": _public_account(account),
        "version": version,
        "profile": _public_profile(saved_profile),
        "ramGb": ram_value,
        "jvmArgsStoredNotExecuted": True,
        "java": {"present": True, "executable": java, "message": "Java is on PATH."},
        "clientJar": str(jar),
        "downloads": [],
        "onlineMode": False,
        "microsoftTokenUsed": False,
    }
    plan_path = data_dir / "launch-plan.json"
    write_json(plan_path, plan)
    log.info(
        f"{reason} LOCAL TEST ACCOUNT '{account['username']}' pid {pid}. "
        "Not sent to an online-mode server."
    )
    return {
        "status": "Started",
        "launched": True,
        "gameStarted": game_started,
        "minecraftClient": game_started,
        "dryRun": False,
        "pid": pid,
        "command": command,
        "errors": [],
        "error": "",
        "planPath": str(plan_path),
        "plan": plan,
    }


def _public_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": account["id"],
        "label": account["label"],
        "username": account["username"],
        "uuid": account["uuid"],
        "mode": account["mode"],
        "onlineModeValid": False,
    }


def _public_profile(saved_profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": saved_profile["name"],
        "intent": saved_profile["intent"],
        "ramGb": saved_profile["ramGb"],
        "shaders": False,
        "modsIntent": saved_profile.get("modsIntent", []),
    }


def _fail(log: VantaLog, data_dir: Path, errors: list[str]) -> dict[str, Any]:
    text = "\n".join(errors)
    log.error(text)
    plan_path = data_dir / "launch-plan.json"
    if plan_path.is_file():
        plan_path.unlink()
    return {
        "status": "Failed",
        "launched": False,
        "gameStarted": False,
        "minecraftClient": False,
        "dryRun": False,
        "pid": None,
        "errors": errors,
        "error": text,
        "planPath": None,
        "plan": None,
    }


def _session_path(data_dir: Path) -> Path:
    return Path(data_dir) / SESSION_NAME


def _read_session(data_dir: Path) -> dict[str, Any]:
    loaded = read_json(_session_path(data_dir), {})
    return loaded if isinstance(loaded, dict) else {}


def _write_session(data_dir: Path, session: dict[str, Any]) -> None:
    write_json(_session_path(data_dir), session)


def _clear_session(data_dir: Path) -> None:
    path = _session_path(data_dir)
    if path.is_file():
        path.unlink()


def _log_tail(path: Path, limit: int = 400) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    text = " ".join(text.split())
    if len(text) > limit:
        return text[-limit:]
    return text


def _version_for_profile(version: dict[str, Any], profile: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Use a profile's instance folder when one was added from the folder dialog.

    The default registry directory is unchanged for profiles that have no
    instance folder. A missing client.jar is an error and does not start Java.
    """
    raw = str(profile.get("instanceDirectory") or "").strip()
    if not raw:
        return version, ""
    instance = Path(raw)
    if not instance.is_dir() or not (instance / "client.jar").is_file():
        return version, (
            f"Instance folder is not usable: {instance}. client.jar was not found. "
            "Minecraft was not started."
        )
    overridden = dict(version)
    overridden["gameDirectory"] = str(instance)
    return overridden, ""



def _required_mod_filenames(dest: Path) -> set[str]:
    """Jars written by instance install. Profile sync must not remove them."""
    path = dest / "required-mods.json"
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    names = data.get("filenames") if isinstance(data, dict) else None
    if not isinstance(names, list):
        return set()
    return {name for name in names if isinstance(name, str) and name == Path(name).name and name.endswith(".jar")}


def _sync_profile_mods(profile_name: str, instance: Path, data_dir: Path) -> None:
    """Copy enabled profile mod jars into the instance mods folder Fabric reads.

    The profile folder stays the source of truth. Disabled jars are not copied.
    Only jars this function wrote last time are removed, so a removed mod does
    not stay loaded and unrelated files in the folder are left alone.
    """
    from vanta.launcher.mods.manager import profile_slug

    dest = instance / "mods"
    dest.mkdir(parents=True, exist_ok=True)
    marker = dest / ".vanta-synced"
    protected = _required_mod_filenames(dest)
    if marker.is_file():
        for name in marker.read_text(encoding="utf-8").splitlines():
            name = name.strip()
            if not name or name != Path(name).name or not name.endswith(".jar"):
                continue
            if name in protected:
                continue
            old = dest / name
            if old.is_file():
                old.unlink()
    source = data_dir / "profiles" / profile_slug(profile_name) / "mods"
    names: list[str] = []
    if source.is_dir():
        for jar_path in sorted(source.glob("*.jar")):
            shutil.copy2(jar_path, dest / jar_path.name)
            names.append(jar_path.name)
    marker.write_text(("\n".join(names) + "\n") if names else "", encoding="utf-8")


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
    return True


def _windows_kernel():
    """kernel32 with pointer-sized handles. Default ctypes restypes truncate them."""
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.GetExitCodeProcess.restype = ctypes.c_bool
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_bool
    kernel.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel.QueryFullProcessImageNameW.restype = ctypes.c_bool
    return kernel


def _windows_pid_alive(pid: int) -> bool:
    """True when the process exists and has not exited. Does not signal it.

    os.kill(pid, 0) on Windows terminates the process, so it is not used.
    """
    import ctypes

    kernel = _windows_kernel()
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


def _windows_command_line(pid: int) -> str:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\").CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip()


def _windows_image_name(pid: int) -> str:
    import ctypes

    kernel = _windows_kernel()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return ""
    try:
        size = ctypes.c_ulong(32768)
        buf = ctypes.create_unicode_buffer(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    finally:
        kernel.CloseHandle(handle)


def _windows_executable(text: str) -> str:
    """First token of a Windows command line, honoring quotes around Program Files."""
    raw = text.strip()
    if raw.startswith('"'):
        end = raw.find('"', 1)
        if end != -1:
            return raw[1:end]
    return raw.split(" ", 1)[0]


def _cmdline_matches(pid: int, jar: str) -> bool:
    if not jar:
        return False
    if os.name == "nt":
        text = _windows_command_line(pid)
        if text:
            base = _windows_executable(text).lower()
            if not (base.endswith("java.exe") or base.endswith("java")):
                return False
            return jar.lower() in text.lower()
        image = _windows_image_name(pid).lower()
        return image.endswith("java.exe") or image.endswith("\\java")
    path = Path(f"/proc/{pid}/cmdline")
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    text = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
    if "java" not in text.split(" ", 1)[0] and "/java" not in text.split(" ", 1)[0]:
        # The executable path can be a symlink ending in java, or "java".
        base = text.split(" ", 1)[0]
        if not base.endswith("java") and "java" not in base:
            return False
    return jar in text


def _signal_stop(pid: int) -> None:
    if pid <= 1 or pid == os.getpid() or pid == os.getppid():
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return
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
    flag = getattr(os, "WNOHANG", None)
    if flag is None:
        return
    try:
        os.waitpid(pid, flag)
    except (ChildProcessError, OSError):
        return
