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
    (r"Incompatible mods? found|requires .* of mod|Mod resolution failed|depends on", "A mod is missing a dependency or doesn't match this Minecraft version. Open Mods and disable the last mod you added."),
    (r"UnsupportedClassVersionError|class file version", "This Minecraft version needs a newer Java. Install Java 21 (1.20.5+) or Java 25 (26.x)."),
    (r"GLFW error|OpenGL|Pixel format not accelerated|WGL", "The graphics driver couldn't start OpenGL. Update the GPU driver; on laptops, run Java on the dedicated GPU."),
    (r"Mixin apply failed|MixinApplyError|InvalidInjectionException", "Two mods conflict (a Mixin failed). Disable recently added mods one by one."),
    (r"Could not reserve enough space|Invalid maximum heap size", "The RAM setting is too high for this PC or Java. Lower RAM for this instance."),
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
    title = desc or (exc.split(":")[0].rsplit(".", 1)[-1] if exc else "Minecraft closed unexpectedly")
    return {"title": title, "exception": exc, "hint": hint or "Open the log below for details, or copy it when asking for help."}


def newest_crash_report(instance: Path, since: float) -> Path | None:
    folder = Path(instance) / "crash-reports"
    if not folder.is_dir():
        return None
    reports = sorted(folder.glob("crash-*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return reports[0] if reports and reports[0].stat().st_mtime >= since - 5 else None


_state: dict[str, Any] = {"pid": 0, "since": 0.0, "jar": "", "crash": None}


def game_status(data_dir: Path, *, lines: int = 200) -> dict[str, Any]:
    data_dir = Path(data_dir)
    session = launch._read_session(data_dir)
    pid = int(session.get("pid") or 0)
    jar = str(session.get("jar") or "")
    running = bool(pid and launch._pid_alive(pid) and launch._cmdline_matches(pid, jar))
    log_lines = _tail_since_start(data_dir / "client.log")
    if running and _state["pid"] != pid:
        _state.update(pid=pid, since=time.time(), jar=jar, crash=None)
    if not running and _state["pid"]:
        # It was running and stopped: decide if that was a crash.
        instance = Path(_state["jar"]).parent if _state["jar"] else None
        report = newest_crash_report(instance, _state["since"]) if instance else None
        joined = "\n".join(log_lines[-80:])
        crashed = bool(report) or bool(re.search(r"Minecraft Crash Report|has crashed|Exception in thread \"(?:main|Render thread)\"|exit code (?!0)\d+", joined))
        if crashed:
            body = report.read_text(encoding="utf-8", errors="replace")[:20000] if report else ""
            _state["crash"] = summarize_crash(log_lines, body) | {"report": str(report) if report else "", "at": int(time.time())}
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
