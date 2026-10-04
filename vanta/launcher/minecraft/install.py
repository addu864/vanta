"""Download Minecraft 1.21.11 and Fabric from their public file hosts.

Mojang's version manifest and the client jar do not require a Microsoft
session. Fabric's meta host does not either. This module does not request
or store a token. PLAY does not call it; a missing client.jar stays a
failed launch.

Run from the repo root:

    python -m vanta.launcher.minecraft.install
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from vanta import __version__ as VANTA_VERSION
from vanta.shared.config.paths import default_data_dir

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META = "https://meta.fabricmc.net/v2/versions/loader"
GAME_VERSION = "1.21.11"
FABRIC_LOADER = "0.19.5"
USER_AGENT = f"Vanta/{VANTA_VERSION} (local installer; no account token)"
# Offline placeholder required by the client argument list. Not a Microsoft token.
OFFLINE_ACCESS_TOKEN = "0"


def install_client(
    game_directory: Path,
    *,
    version: str = GAME_VERSION,
    loader: str = FABRIC_LOADER,
    download_assets: bool = False,
) -> dict[str, Any]:
    """Place client.jar, libraries, and version metadata under the instance dir."""
    instance = Path(game_directory) / f"{version}-fabric"
    instance.mkdir(parents=True, exist_ok=True)
    version_meta = _fetch_json(MANIFEST_URL)
    entry = next((item for item in version_meta["versions"] if item.get("id") == version), None)
    if entry is None:
        raise RuntimeError(f"Mojang manifest has no {version} entry.")
    vanilla = _fetch_json(entry["url"])
    vanilla_path = instance / f"{version}.json"
    vanilla_path.write_text(json.dumps(vanilla), encoding="utf-8")

    client = vanilla["downloads"]["client"]
    client_jar = instance / "client.jar"
    _download(client["url"], client_jar, sha1=client.get("sha1"), size=client.get("size"))

    libraries = instance / "libraries"
    kept = 0
    for library in vanilla.get("libraries", []):
        if not _rules_allow(library.get("rules")):
            continue
        artifact = (library.get("downloads") or {}).get("artifact")
        if not artifact or not artifact.get("path") or not artifact.get("url"):
            continue
        destination = libraries / artifact["path"]
        _download(artifact["url"], destination, sha1=artifact.get("sha1"), size=artifact.get("size"))
        kept += 1

    fabric_url = f"{FABRIC_META}/{version}/{loader}/profile/json"
    fabric = _fetch_json(fabric_url)
    fabric_path = instance / "fabric-profile.json"
    fabric_path.write_text(json.dumps(fabric), encoding="utf-8")
    fabric_count = 0
    for library in fabric.get("libraries", []):
        relative = _maven_relative(library["name"])
        base = str(library.get("url") or "https://maven.fabricmc.net/").rstrip("/") + "/"
        destination = libraries / relative
        _download(base + relative, destination, sha1=library.get("sha1"), size=library.get("size"))
        fabric_count += 1

    asset_index = vanilla.get("assetIndex") or {}
    assets_root = instance / "assets"
    index_id = str(asset_index.get("id") or "")
    asset_count = 0
    if index_id and asset_index.get("url"):
        indexes = assets_root / "indexes"
        index_path = indexes / f"{index_id}.json"
        _download(asset_index["url"], index_path, sha1=asset_index.get("sha1"), size=asset_index.get("size"))
        if download_assets:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            objects = list((index.get("objects") or {}).values())

            def _one(obj: dict[str, Any]) -> None:
                digest = str(obj["hash"])
                obj_url = f"https://resources.download.minecraft.net/{digest[:2]}/{digest}"
                _download(
                    obj_url,
                    assets_root / "objects" / digest[:2] / digest,
                    sha1=digest,
                    size=obj.get("size"),
                )

            workers = min(8, max(1, len(objects)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(_one, objects))
            asset_count = len(objects)

    required_mods = None
    # Only instances this installer creates. Linking an existing folder does not call this.
    if version == GAME_VERSION:
        from vanta.launcher.mods.required import install_required_mods

        required_mods = install_required_mods(instance / "mods")

    return {
        "ok": True,
        "instance": str(instance),
        "requiredMods": required_mods,
        "clientJar": str(client_jar),
        "vanillaLibraries": kept,
        "fabricLibraries": fabric_count,
        "assetsDownloaded": asset_count,
        "assetIndex": index_id,
        "loader": loader,
        "version": version,
        "sources": [entry["url"], fabric_url],
    }


def build_official_command(
    *,
    java: str,
    memory: list[str],
    instance: Path,
    client_jar: Path,
    vanilla_path: Path,
    fabric_path: Path | None,
    account: dict[str, Any],
    version_number: str,
    ram_gb: int,
) -> list[str]:
    """Classpath launch. No server address is added. Stored jvmArgs are not used."""
    del ram_gb  # memory flags are already in ``memory``
    vanilla = json.loads(vanilla_path.read_text(encoding="utf-8"))
    fabric = json.loads(fabric_path.read_text(encoding="utf-8")) if fabric_path and fabric_path.is_file() else None
    libraries = instance / "libraries"
    classpath: list[str] = []
    if fabric:
        for library in fabric.get("libraries", []):
            path = libraries / _maven_relative(library["name"])
            if path.is_file():
                classpath.append(str(path))
    for library in vanilla.get("libraries", []):
        if not _rules_allow(library.get("rules")):
            continue
        artifact = (library.get("downloads") or {}).get("artifact")
        if not artifact or not artifact.get("path"):
            continue
        path = libraries / artifact["path"]
        if path.is_file():
            classpath.append(str(path))
    classpath.append(str(client_jar))

    natives = instance / "natives"
    natives.mkdir(parents=True, exist_ok=True)
    assets_root = instance / "assets"
    asset_index = str((vanilla.get("assetIndex") or {}).get("id") or "")
    values = {
        "natives_directory": str(natives),
        "launcher_name": "Vanta",
        "launcher_version": VANTA_VERSION,
        "classpath": os.pathsep.join(classpath),
        "library_directory": str(libraries),
        "classpath_separator": os.pathsep,
        "auth_player_name": str(account.get("username") or "Player"),
        "version_name": version_number,
        "game_directory": str(instance),
        "assets_root": str(assets_root),
        "assets_index_name": asset_index,
        "auth_uuid": str(account.get("uuid") or "").replace("-", ""),
        "auth_access_token": OFFLINE_ACCESS_TOKEN,
        "clientid": "0",
        "auth_xuid": "0",
        "version_type": str(vanilla.get("type") or "release"),
        "user_type": "legacy",
    }

    main_class = str(vanilla.get("mainClass") or "net.minecraft.client.main.Main")
    jvm_args: list[str] = []
    game_args: list[str] = []
    if fabric:
        raw_main = fabric.get("mainClass")
        if isinstance(raw_main, dict):
            main_class = str(raw_main.get("client") or main_class)
        elif isinstance(raw_main, str) and raw_main:
            main_class = raw_main
        jvm_args.extend(_argument_list(fabric.get("arguments", {}).get("jvm") or []))
        game_args.extend(_argument_list(fabric.get("arguments", {}).get("game") or []))
    arguments = vanilla.get("arguments") or {}
    jvm_args.extend(_argument_list(arguments.get("jvm") or []))
    game_args.extend(_argument_list(arguments.get("game") or []))
    if not arguments.get("game") and vanilla.get("minecraftArguments"):
        game_args.extend(str(vanilla["minecraftArguments"]).split())

    command = [java, *memory]
    for arg in jvm_args:
        command.append(_substitute(arg, values))
    command.append(main_class)
    for arg in game_args:
        command.append(_substitute(arg, values))
    # Never append a multiplayer server. The local account is not an online session.
    return command


def _argument_list(items: list[Any]) -> list[str]:
    values: list[str] = []
    for item in items:
        if isinstance(item, str):
            values.append(item)
            continue
        if not isinstance(item, dict):
            continue
        if not _rules_allow(item.get("rules")):
            continue
        value = item.get("value")
        if isinstance(value, list):
            values.extend(str(part) for part in value)
        elif isinstance(value, str):
            values.append(value)
    return values


def _substitute(value: str, values: dict[str, str]) -> str:
    rendered = value
    for key, replacement in values.items():
        rendered = rendered.replace("${" + key + "}", replacement)
    return rendered


def _rules_allow(rules: list[dict[str, Any]] | None) -> bool:
    if not rules:
        return True
    allow = False
    for rule in rules:
        if _rule_matches(rule):
            allow = rule.get("action") == "allow"
    return allow


def _rule_matches(rule: dict[str, Any]) -> bool:
    if "features" in rule:
        return False
    os_rule = rule.get("os")
    if not isinstance(os_rule, dict):
        return True
    name = os_rule.get("name")
    if name and name != _os_name():
        return False
    arch = os_rule.get("arch")
    if arch and arch != _arch_name():
        return False
    return True


def _os_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "osx"
    if system.startswith("win"):
        return "windows"
    return "linux"


def _arch_name() -> str:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "x64"
    if machine in {"i386", "i686", "x86"}:
        return "x86"
    if machine in {"aarch64", "arm64"}:
        return "arm64"
    return machine


def _maven_relative(name: str) -> str:
    parts = name.split(":")
    if len(parts) < 3:
        raise ValueError(f"Not a maven coordinate: {name}")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    filename = f"{artifact}-{version}"
    if classifier:
        filename += f"-{classifier}"
    filename += ".jar"
    return f"{group.replace('.', '/')}/{artifact}/{version}/{filename}"


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected a JSON object from {url}")
    return data


def _download(url: str, destination: Path, *, sha1: str | None, size: int | None) -> None:
    """Download one file. A finished file with the expected size and SHA1 is kept.

    A stalled read is retried. A partial ``.part`` file is resumed with HTTP
    Range when the server allows it, so a timeout does not start that file over.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and _matches(destination, sha1=sha1, size=size):
        return
    tmp = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for _attempt in range(8):
        try:
            _transfer(url, tmp, size=size)
            if sha1 and _sha1(tmp) != sha1:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"SHA1 mismatch for {destination.name}")
            if size is not None and tmp.stat().st_size != int(size):
                raise TimeoutError(f"Short download for {destination.name}")
            tmp.replace(destination)
            return
        except Exception as exc:  # noqa: BLE001 - retry network and checksum failures
            last_error = exc
    raise RuntimeError(f"Download failed for {destination.name}: {last_error}") from last_error


def _matches(path: Path, *, sha1: str | None, size: int | None) -> bool:
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != int(size):
        return False
    if sha1 and _sha1(path) != sha1:
        return False
    return size is not None or sha1 is not None or path.stat().st_size > 0


def _transfer(url: str, tmp: Path, *, size: int | None) -> None:
    have = tmp.stat().st_size if tmp.is_file() else 0
    if size is not None and have > int(size):
        tmp.unlink(missing_ok=True)
        have = 0
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=45) as response:
        code = int(getattr(response, "status", None) or response.getcode())
        if code == 416 and size is not None and have == int(size):
            return
        if code == 200 and have:
            have = 0
            mode = "wb"
        elif code == 206 and have:
            mode = "ab"
        elif code == 200:
            mode = "wb"
        else:
            raise RuntimeError(f"HTTP {code} for {url}")
        with tmp.open(mode) as handle:
            while True:
                chunk = response.read(1024 * 128)
                if not chunk:
                    break
                handle.write(chunk)


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 256)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    data = default_data_dir()
    settings_path = data / "config.json"
    game = data / "game"
    if settings_path.is_file():
        stored = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(stored, dict) and stored.get("gameDirectory"):
            game = Path(str(stored["gameDirectory"]))
    result = install_client(game, download_assets=False)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
