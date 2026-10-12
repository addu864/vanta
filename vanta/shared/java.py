"""Find a Java runtime on Windows, macOS and Linux.

Order: JAVA_HOME, then (macOS) ``/usr/libexec/java_home`` and the
JavaVirtualMachines folders, then PATH, then common Windows install folders.
On macOS ``/usr/bin/java`` is only a stub when no JDK is installed, so it is
used only if ``java_home`` reports a real JDK.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

MAC_STUB = "/usr/bin/java"
MAC_JVM_DIRS = ("/Library/Java/JavaVirtualMachines", "~/Library/Java/JavaVirtualMachines")
WINDOWS_VENDORS = ("Eclipse Adoptium", "Java", "Microsoft", "Zulu", "BellSoft", "Amazon Corretto")


def is_mac() -> bool:
    return platform.system() == "Darwin"


def is_windows() -> bool:
    return os.name == "nt"


def _exe() -> str:
    return "java.exe" if is_windows() else "java"


def _java_home_bin(home: str | os.PathLike[str] | None) -> str | None:
    if not home:
        return None
    candidate = Path(home) / "bin" / _exe()
    return str(candidate) if candidate.is_file() else None


def mac_java_home(runner=subprocess.run) -> str | None:
    """``/usr/libexec/java_home -v 21+`` (falls back to any version)."""
    tool = "/usr/libexec/java_home"
    if not Path(tool).exists():
        return None
    for args in ([tool, "-v", "21+"], [tool]):
        try:
            done = runner(args, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        home = (done.stdout or "").strip()
        if done.returncode == 0 and home:
            return home
    return None


def mac_jvm_candidates(dirs=MAC_JVM_DIRS) -> list[str]:
    """java binaries under JavaVirtualMachines, newest-looking first."""
    found: list[str] = []
    for raw in dirs:
        base = Path(os.path.expanduser(raw))
        if not base.is_dir():
            continue
        for java in sorted(base.glob("*/Contents/Home/bin/java"), reverse=True):
            if java.is_file():
                found.append(str(java))
    return found


def find_java(*, runner=subprocess.run, which=shutil.which, mac: bool | None = None) -> str | None:
    mac = is_mac() if mac is None else mac
    from_home = _java_home_bin(os.environ.get("JAVA_HOME"))
    if from_home:
        return from_home
    if mac:
        home_bin = _java_home_bin(mac_java_home(runner))
        if home_bin:
            return home_bin
        candidates = mac_jvm_candidates()
        if candidates:
            return candidates[0]
    found = which("java")
    if found and not (mac and os.path.realpath(found) == MAC_STUB):
        return found
    if is_windows():
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for root in filter(None, roots):
            for vendor in WINDOWS_VENDORS:
                base = Path(root) / vendor
                if base.is_dir():
                    for candidate in sorted(base.glob("*/bin/java.exe"), reverse=True):
                        return str(candidate)
    return None


# ---- Several Javas: pick one that suits a Minecraft version -----------------

LINUX_JVM_DIRS = ("/usr/lib/jvm", "/usr/java", "/opt/java", "~/jre*", "~/.jdks", "~/.sdkman/candidates/java")


def java_major(java: str, runner=subprocess.run) -> int | None:
    """Major version from the JDK ``release`` file, else ``java -version``."""
    path = Path(java)
    for home in (path.parent.parent, path.resolve().parent.parent):
        release = home / "release"
        if release.is_file():
            try:
                for line in release.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("JAVA_VERSION="):
                        return _parse_major(line.split("=", 1)[1].strip().strip('"'))
            except OSError:
                pass
    try:
        done = runner([java, "-version"], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    text = (done.stderr or "") + (done.stdout or "")
    import re

    match = re.search(r'version "([^"]+)"', text)
    return _parse_major(match.group(1)) if match else None


def _parse_major(value: str) -> int | None:
    parts = value.split(".")
    try:
        first = int(parts[0].split("-")[0].split("+")[0])
    except ValueError:
        return None
    if first == 1 and len(parts) > 1:  # 1.8.0_x -> 8
        try:
            return int(parts[1])
        except ValueError:
            return None
    return first


def candidate_javas(*, which=shutil.which, runner=subprocess.run, mac: bool | None = None) -> list[str]:
    """Every java executable Vanta can see, without duplicates."""
    mac = is_mac() if mac is None else mac
    found: list[str] = []

    def add(path: str | None) -> None:
        if path and Path(path).is_file():
            real = os.path.realpath(path)
            if mac and real == MAC_STUB:
                return
            if real not in {os.path.realpath(p) for p in found}:
                found.append(path)

    add(_java_home_bin(os.environ.get("JAVA_HOME")))
    if mac:
        add(_java_home_bin(mac_java_home(runner)))
        for java in mac_jvm_candidates():
            add(java)
    add(which("java"))
    if is_windows():
        roots = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for root in filter(None, roots):
            for vendor in WINDOWS_VENDORS:
                base = Path(root) / vendor
                if base.is_dir():
                    for candidate in sorted(base.glob("*/bin/java.exe"), reverse=True):
                        add(str(candidate))
    elif not mac:
        for raw in LINUX_JVM_DIRS:
            pattern = os.path.expanduser(raw)
            parent = Path(pattern).parent if "*" in pattern else Path(pattern)
            glob = Path(pattern).name if "*" in pattern else "*"
            if parent.is_dir():
                for home in sorted(parent.glob(glob), reverse=True):
                    add(str(home / "bin" / "java"))
    return found


def list_javas(**kwargs) -> list[dict[str, object]]:
    runner = kwargs.get("runner", subprocess.run)
    items = []
    for path in candidate_javas(**kwargs):
        items.append({"path": path, "major": java_major(path, runner=runner)})
    return items


def select_java(required: int, javas: list[dict[str, object]] | None = None) -> tuple[str | None, str]:
    """Smallest installed major >= required (closest match is safest for older Minecraft).

    Returns (path, "") or (None, error message).
    """
    javas = list_javas() if javas is None else javas
    usable = [j for j in javas if isinstance(j.get("major"), int) and int(j["major"]) >= required]
    if usable:
        best = min(usable, key=lambda j: int(j["major"]))
        return str(best["path"]), ""
    seen = ", ".join(f"Java {j.get('major') or '?'}" for j in javas) or "none"
    return None, (
        f"This Minecraft version needs Java {required} or newer, but Vanta found: {seen}. "
        f"Install Java {required}+ (https://adoptium.net/temurin/releases/) and try again."
    )
