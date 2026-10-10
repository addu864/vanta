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
