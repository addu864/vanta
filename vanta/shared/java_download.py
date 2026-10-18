"""Download Eclipse Temurin (Adoptium) for the right OS and CPU when no suitable Java is installed.

The Adoptium API gives a package link and its SHA-256; the archive is checked before it is
unpacked into <data>/runtime/temurin-<major>-<arch>/. Nothing outside that folder is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable

from vanta.shared import macplatform

API = "https://api.adoptium.net/v3/assets/latest/{major}/hotspot?architecture={arch}&image_type={image}&os={os}&vendor=eclipse"
USER_AGENT = "Vanta-Launcher"
# Minecraft versions before 1.19 ship LWJGL 3.2 without macos-arm64 natives.
FIRST_ARM64_MAC_MC = (1, 19)


def mc_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for p in (version or "").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def lwjgl_has_mac_arm64(mc_version: str) -> bool:
    t = mc_tuple(mc_version)
    return t[0] >= 2 or t[:2] >= FIRST_ARM64_MAC_MC  # 26.x and newer use the year scheme


def adoptium_os(system: str) -> str:
    return {"Darwin": "mac", "Windows": "windows"}.get(system, "linux")


def adoptium_arch(arch: str) -> str:
    return {"x64": "x64", "arm64": "aarch64"}.get(arch, arch)


def java_arch_for(mc_version: str, major: int, info: dict[str, Any]) -> tuple[str, bool]:
    """(arch, strict). strict=True means another arch will not work for this Minecraft version.

    Intel Mac: always x64. Apple Silicon: arm64, except Minecraft < 1.19 and Java 8, which need
    x64 Java running under Rosetta 2 (LWJGL 3.2 has no arm64 Mac natives).
    """
    hw = info.get("hardware") or "x64"
    if info.get("mac") and hw == macplatform.ARM64:
        if major <= 8 or not lwjgl_has_mac_arm64(mc_version):
            return macplatform.X64, True
        return macplatform.ARM64, False
    if info.get("mac"):
        return macplatform.X64, True  # an Intel Mac cannot run arm64 code at all
    return hw, False


def api_url(major: int, os_name: str, arch: str, image: str = "jre") -> str:
    return API.format(major=int(major), arch=adoptium_arch(arch), image=image, os=os_name)


def pick_package(assets: Any) -> dict[str, Any]:
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("Adoptium has no build for this system.")
    for asset in assets:
        pkg = ((asset or {}).get("binary") or {}).get("package") or {}
        link, sha, name = pkg.get("link"), pkg.get("checksum"), pkg.get("name")
        if link and sha and name and str(link).startswith("https://") and str(name).endswith((".tar.gz", ".zip")):
            return {"link": link, "sha256": str(sha).lower(), "name": name, "size": pkg.get("size"),
                    "release": (asset.get("version") or {}).get("openjdk_version") or asset.get("release_name")}
    raise RuntimeError("Adoptium returned no downloadable package.")


def runtime_root(data_dir: Path) -> Path:
    return Path(data_dir) / "runtime"


def java_in(home: Path) -> Path | None:
    for rel in ("Contents/Home/bin/java", "bin/java", "bin/java.exe"):
        cand = home / rel
        if cand.is_file():
            return cand
    return None


def _find_java(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    return java_in(folder) or next((j for p in sorted(folder.iterdir()) if p.is_dir() and (j := java_in(p))), None)


def installed_runtimes(data_dir: Path) -> list[str]:
    root = runtime_root(data_dir)
    if not root.is_dir():
        return []
    return [str(j) for folder in sorted(root.glob("temurin-*"), reverse=True) if (j := _find_java(folder))]


def _safe_extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    target = target.resolve()
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for m in z.namelist():
                if not (target / m).resolve().is_relative_to(target):
                    raise RuntimeError("Unsafe path in Java archive.")
            z.extractall(target)
        return
    with tarfile.open(archive, "r:gz") as t:
        members = []
        for m in t.getmembers():
            dest = (target / m.name).resolve()
            if not dest.is_relative_to(target) or m.isdev():
                raise RuntimeError("Unsafe path in Java archive.")
            if m.issym() and not (dest.parent / m.linkname).resolve().is_relative_to(target):
                raise RuntimeError("Unsafe link in Java archive.")
            if m.islnk() and not (target / m.linkname).resolve().is_relative_to(target):
                raise RuntimeError("Unsafe link in Java archive.")
            members.append(m)
        if hasattr(tarfile, "tar_filter"):
            t.extractall(target, members=members, filter="tar")
        else:
            t.extractall(target, members=members)


def make_runnable(java: Path, *, mac: bool | None = None, runner: Callable[..., Any] | None = None) -> None:
    """Executable bits for java and its helpers, and (macOS) no quarantine flag on the runtime.

    Archives unpacked by Python normally keep their modes and get no quarantine flag, but a
    runtime copied in by hand (or unpacked by Finder) can lose the x bit or carry
    com.apple.quarantine, and then the JVM or jspawnhelper is refused.
    """
    import platform as _platform
    import subprocess
    home = java.parent.parent  # .../Contents/Home or the runtime root
    for exe in [java, *sorted((home / "bin").glob("*")), home / "lib" / "jspawnhelper"]:
        try:
            if exe.is_file() and not exe.is_symlink():
                exe.chmod(exe.stat().st_mode | 0o755)
        except OSError:
            pass
    mac = (_platform.system() == "Darwin") if mac is None else mac
    if mac:
        root = home.parent.parent if home.name == "Home" and home.parent.name == "Contents" else home
        try:
            (runner or subprocess.run)(["xattr", "-dr", "com.apple.quarantine", str(root)],
                                       capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            pass


def _get(url: str, timeout: int = 60):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)


def download_temurin(data_dir: Path, major: int, arch: str, *, system: str | None = None,
                     fetch: Callable[[str], Any] | None = None, opener: Callable[..., Any] | None = None,
                     progress: Callable[[int, int], None] | None = None) -> str:
    """Download + verify + unpack. Returns the java executable path."""
    import platform as _platform
    os_name = adoptium_os(system or _platform.system())
    fetch = fetch or (lambda url: json.loads(_get(url).read().decode("utf-8")))
    opener = opener or _get
    pkg = pick_package(fetch(api_url(major, os_name, arch)))
    root = runtime_root(data_dir)
    final = root / f"temurin-{major}-{arch}"
    tmp_dir = root / f".tmp-{major}-{arch}-{os.getpid()}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    archive = tmp_dir / Path(pkg["name"]).name
    digest = hashlib.sha256()
    try:
        with opener(pkg["link"], timeout=120) as resp, archive.open("wb") as out:
            total = int(getattr(resp, "headers", {}).get("Content-Length") or pkg.get("size") or 0)
            done = 0
            while chunk := resp.read(1 << 16):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if digest.hexdigest() != pkg["sha256"]:
            raise RuntimeError("The Java download failed its SHA-256 check and was deleted.")
        unpack = tmp_dir / "x"
        _safe_extract(archive, unpack)
        java = _find_java(unpack)
        if java is None:
            raise RuntimeError("The Java archive had no java executable.")
        if os_name != "windows":
            make_runnable(java)
        shutil.rmtree(final, ignore_errors=True)
        unpack.rename(final)
        (final / "vanta-runtime.json").write_text(json.dumps({"major": major, "arch": arch, "os": os_name,
                                                              "release": pkg.get("release"), "sha256": pkg["sha256"]}))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return str(_find_java(final))


# ---- background job (one per major+arch) ----
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def start(data_dir: Path, major: int, arch: str, runner: Callable[..., str] | None = None) -> dict[str, Any]:
    key = f"{major}-{arch}"
    with _lock:
        job = _jobs.get(key)
        if job and job["state"] in {"running", "done"}:
            return dict(job)
        job = _jobs[key] = {"id": key, "major": major, "arch": arch, "state": "running", "percent": 0, "error": "",
                            "started": time.time()}

    def work() -> None:
        def prog(done: int, total: int) -> None:
            job["percent"] = int(done * 100 / total) if total else 0
        try:
            job["java"] = (runner or download_temurin)(data_dir, major, arch, progress=prog)
            job.update(state="done", percent=100)
        except Exception as exc:  # noqa: BLE001 - shown to the player
            job.update(state="failed", error=str(exc))

    threading.Thread(target=work, name=f"vanta-java-{key}", daemon=True).start()
    return dict(job)


def status(major: int, arch: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(f"{major}-{arch}")
        return dict(job) if job else None
