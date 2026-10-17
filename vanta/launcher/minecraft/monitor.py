"""Game-running indicator, live log tail and friendly crash summaries."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from vanta.launcher.minecraft import launch

START_MARK = "--- local start "
HINTS: tuple[tuple[str, str], ...] = (
    (r"OutOfMemoryError|Java heap space", "Minecraft ran out of memory. Raise RAM for this instance (or close other apps)."),
    (r"No space left on device|not enough space on the disk", "The disk is full. Free some space, then start Minecraft again."),
    (r"Could not reserve enough space|Invalid maximum heap size", "The RAM setting is too high for this PC or Java. Lower RAM for this instance."),
    (r"[Dd]uplicate mods?\b|is supplied by multiple|Found \d+ duplicate", "The same mod is installed twice. Open the instance's mods folder and remove the older copy."),
    (r"fabric-api.{0,80}(?:which is missing|is missing)|requires .{0,40}\b(?:fabric-api|fabric api)\b", "A mod needs Fabric API. Install Fabric API for this version from MODS."),
    (r"optifabric|OptiFine", "OptiFine doesn't work with Sodium and Iris. Remove OptiFine/OptiFabric; Iris loads shaders instead."),
    (r"mods\.toml|is a (?:Neo)?Forge mod|forge mod", "A Forge or NeoForge mod is in a Fabric instance. Remove it, or find the Fabric version on Modrinth."),
    (r"Incompatible mods? found|requires .* of mod|Mod resolution failed|depends on|mods are incompatible", "A mod is missing a dependency or doesn't match this Minecraft version. Open Mods and disable the last mod you added."),
    (r"UnsupportedClassVersionError|class file version", "This Minecraft version needs a newer Java. Install Java 21 (1.20.5+) or Java 25 (26.x)."),
    (r"InaccessibleObjectException|module java\.base does not \"?opens", "The Java version is too new for this Minecraft or a mod. Use the Java Vanta picks (17 for 1.18–1.20.4, 21 for 1.20.5+)."),
    (r"Failed to (?:compile|link) (?:shader|program)|[Ss]hader compil|iris.{0,40}(?:shader|program).{0,40}(?:error|fail)", "The shader pack failed to load. Press K in game to turn shaders off, or pick another pack in Video Settings › Shader Packs."),
    (r"(?:EXCEPTION_ACCESS_VIOLATION|SIGSEGV).{0,400}(?:nvoglv|atio6axx|atioglxx|ig\d+icd|igxelp|libGL|amdxx)|(?:nvoglv|atio6axx|ig\d+icd)\w*\.dll", "The graphics driver crashed. Update the GPU driver; turning shaders off often helps."),
    (r"GLFW error|OpenGL|Pixel format not accelerated|WGL", "The graphics driver couldn't start OpenGL. Update the GPU driver; on laptops, run Java on the dedicated GPU."),
    (r"NSInternalInconsistencyException|must be (?:called|used) (?:on|from) the main thread|XstartOnFirstThread", "macOS refused to open the game window. Start the game from Vanta (it adds -XstartOnFirstThread) and update Vanta if this keeps happening."),
    (r"Mixin apply failed|MixinApplyError|InvalidInjectionException", "Two mods conflict (a Mixin failed). Disable recently added mods one by one."),
    (r"NoSuchMethodError|NoSuchFieldError|NoClassDefFoundError|ClassNotFoundException", "A mod was built for a different Minecraft version, or needs another mod that isn't installed. Update or disable the mod named in the error."),
    (r"JsonSyntaxException|MalformedJsonException|Failed to (?:load|read|parse) (?:the )?config|ConfigLoadingException|ParsingException", "A mod's config file is damaged. Open the game folder, move that mod's file out of the config folder, and start again; it is recreated."),
    (r"Exception (?:reading|loading) .{0,40}\.mca|RegionFile|Failed to load level|level\.dat", "A world file looks damaged. Restore a backup in INSTANCES › Worlds, or try another world."),
    (r"Ticking (?:block )?entity|Ticking entity|Ticking player", "Something in the world (a block or mob) crashes the game every tick. Back up the world, then update or remove the mod that adds it."),
    (r"ZipException|zip END header not found|invalid LOC header|error in opening zip file", "A game or mod file is damaged (often a half-finished download). Use Check mods on the crash card; if no mod is damaged, reinstall this Minecraft version."),
    (r"Could not find or load main class", "The Minecraft files for this version are incomplete. Reinstall this version, then press PLAY again."),
    (r"AccessDeniedException|Access is denied|Operation not permitted", "The system blocked access to a game file. Close other launchers or games, and if antivirus quarantined something, allow the Vanta folder."),
    (r"BindException|Address already in use", "Another program is already using that network port (often a second Minecraft or a server). Close it, or pick another port."),
    (r"StackOverflowError", "A mod got stuck in a loop (stack overflow). Disable recently added mods one by one."),
    (r"Failed to (?:bind|open) .*OpenAL|OpenAL", ""),
)



def _tail_since_start(path: Path, limit: int = 300) -> list[str]:
    if not path.is_file():
        return []
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 400_000))
        text = fh.read().decode("utf-8", "replace")
    idx = text.rfind(START_MARK)
    if idx >= 0:
        text = text[idx:]
    return text.splitlines()[-limit:]


_SUSPECT_PATTERNS = (
    r"Mixin apply for mod ([a-z0-9_\-]{2,64}) failed",
    r"from mod ([a-z0-9_\-]{2,64})\b",
    r"Suspected Mods?: ([^\n]+)",
    r"Mod '[^']+' \(([a-z0-9_\-]{2,64})\)",
    r"\b([a-z0-9_\-]{2,64})\.mixins\.json",
)
_NOT_SUSPECTS = {"minecraft", "java", "fabricloader", "fabric", "mixinextras", "none", "unknown", "fabric-api", "vanta"}


def suspect_mods(text: str, limit: int = 4) -> list[str]:
    """Mod ids named by Mixin failures, Fabric's resolution errors or the 'Suspected Mods' line."""
    found: list[str] = []
    for pattern in _SUSPECT_PATTERNS:
        for m in re.finditer(pattern, text or "", re.I):
            raw = m.group(1)
            parts = re.findall(r"[A-Za-z0-9_\-]{2,64}", raw) if pattern.startswith("Suspected") else [raw]
            for part in parts:
                ident = part.lower()
                if ident not in _NOT_SUSPECTS and not ident.startswith("fabric-") and ident not in found:
                    found.append(ident)
    return found[:limit]


def mod_files_for(instance: Path, ids: list[str]) -> list[dict[str, str]]:
    """Match mod ids to jars in the instance (reads fabric.mod.json, falls back to the file name)."""
    import json
    import zipfile

    out: list[dict[str, str]] = []
    folder = Path(instance) / "mods"
    if not ids or not folder.is_dir():
        return out
    for jar in sorted(folder.glob("*.jar")):
        mod_id = ""
        try:
            with zipfile.ZipFile(jar) as zf:
                mod_id = str(json.loads(zf.read("fabric.mod.json").decode("utf-8", "replace")).get("id") or "").lower()
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            pass
        flat = re.sub(r"[-_ ]", "", jar.name.lower())
        for ident in ids:
            if ident == mod_id or (not mod_id and flat.startswith(re.sub(r"[-_ ]", "", ident))):
                out.append({"id": ident, "file": jar.name})
                break
    return out


def summarize_crash(lines: list[str], crash_report: str = "") -> dict[str, Any]:
    text = crash_report or "\n".join(lines)
    desc = ""
    m = re.search(r"(?m)^Description: (.+)$", crash_report)
    if m:
        desc = m.group(1).strip()
    exc = ""
    for line in (crash_report.splitlines() if crash_report else lines):
        s = line.strip()
        if re.match(r"^(?:Caused by: )?[a-zA-Z_$][\w$.]*(?:Exception|Error)\b", s) or "Exception in thread" in s:
            exc = s[:300]
            if not crash_report:
                continue
            break
    hint = ""
    for pattern, message in HINTS:
        if message and re.search(pattern, text, re.I):
            hint = message
            break
    details: list[str] = []
    if re.search(r"Incompatible mods? found|mods are incompatible", text, re.I):
        # Fabric's own explanation, e.g. "Mod 'X' (x) 1.0 requires ... which is missing!"
        details = [m.strip() for m in re.findall(r"(?m)^\s*- (.+)$", text)][:6]
        title = "Mods are incompatible"
    else:
        title = desc or (exc.split(":")[0].rsplit(".", 1)[-1] if exc else "Minecraft closed unexpectedly")
    if details:
        exc = "\n".join(details)
    return {"title": title, "exception": exc, "hint": hint or "Open the log below for details, or copy it when asking for help.",
            "details": details, "suspects": suspect_mods(text)}


def newest_crash_report(instance: Path, since: float) -> Path | None:
    folder = Path(instance) / "crash-reports"
    if not folder.is_dir():
        return None
    reports = sorted(folder.glob("crash-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return reports[0] if reports and reports[0].stat().st_mtime >= since - 5 else None


_state: dict[str, Any] = {"pid": 0, "since": 0.0, "jar": "", "crash": None, "seen": 0.0}


def game_status(data_dir: Path, *, lines: int = 200) -> dict[str, Any]:
    data_dir = Path(data_dir)
    session = launch._read_session(data_dir)
    pid = int(session.get("pid") or 0)
    jar = str(session.get("jar") or "")
    running = bool(pid and launch._pid_alive(pid) and launch._cmdline_matches(pid, jar))
    log_lines = _tail_since_start(data_dir / "client.log")
    if running and _state["pid"] != pid:
        _state.update(pid=pid, since=float(session.get("started") or time.time()), jar=jar, crash=None, seen=time.time())
    elif not running and pid and not _state["pid"]:
        # The game exited before anyone polled (a crash within a second or two),
        # or Vanta restarted while it ran: judge that session now.
        _state.update(pid=pid, since=float(session.get("started") or time.time() - 600), jar=jar, crash=None,
                      seen=float(session.get("started") or 0))
    if running:
        _state["seen"] = time.time()
    if not running and _state["pid"]:
        try:
            from vanta.launcher.minecraft import playtime

            folder = Path(_state["jar"]).parent if _state["jar"] else None
            if folder is not None and _state["since"]:
                playtime.record(data_dir, folder, _state["since"], playtime.session_end(folder, _state["since"], _state.get("seen") or 0))
        except Exception:  # noqa: BLE001 - playtime must never break crash detection
            pass
        # It was running and stopped: decide if that was a crash.
        instance = Path(_state["jar"]).parent if _state["jar"] else None
        report = newest_crash_report(instance, _state["since"]) if instance else None
        joined = "\n".join(log_lines[-80:])
        code = launch.exit_code(_state["pid"])
        if _state["pid"] in launch.STOPPED_BY_USER:
            code = None
        crashed = (_state["pid"] not in launch.STOPPED_BY_USER) and ((code not in (None, 0)) or bool(report) or bool(re.search(
            r"Minecraft Crash Report|has crashed|Exception in thread \"(?:main|Render thread)\"|exit code (?!0)\d+|Incompatible mods? found|Mod resolution failed", joined)))
        if crashed:
            body = report.read_text(encoding="utf-8", errors="replace")[:20000] if report else ""
            info = summarize_crash(log_lines, body)
            info["suspectFiles"] = mod_files_for(instance, info["suspects"]) if instance else []
            _state["crash"] = info | {"report": str(report) if report else "", "at": int(time.time()), "exitCode": code,
                                      "instance": instance.name if instance else ""}
        _state.update(pid=0)
        launch._clear_session(data_dir)
    return {
        "ok": True,
        "running": running,
        "pid": pid if running else None,
        "since": int(_state["since"]) if running else None,
        "log": log_lines[-lines:],
        "crash": _state.get("crash"),
    }


def dismiss_crash() -> None:
    _state["crash"] = None
