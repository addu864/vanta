"""v1.3.2: Skindex skins download on demand; Mojang defaults come from client.jar."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from vanta.launcher.skins.builtin import VantaSkinLibrary
from vanta.launcher.skins.repository import SkinRepositoryError
from vanta.shared.config.paths import repo_root


def _png() -> bytes:
    import struct, zlib
    raw = b"".join(b"\x00" + b"\x00\x00\x00\xff" * 64 for _ in range(64))
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_no_skindex_pngs_shipped() -> None:
    assert not (repo_root() / "assets" / "skins" / "skindex").exists()


def test_skindex_downloads_once_then_cached(tmp_path: Path) -> None:
    calls: list[str] = []
    png = _png()
    lib = VantaSkinLibrary(data_dir=tmp_path, fetch=lambda url: calls.append(url) or png)
    skin = next(h for h in lib.catalog() if h.id.startswith("skindex-"))
    assert lib.fetch_texture(skin.id) == png
    assert lib.fetch_texture(skin.id) == png
    assert len(calls) == 1 and calls[0].startswith("https://www.skindex.pro/static/skins/")
    assert list((tmp_path / "skin-cache" / "skindex").glob("*.png"))


def test_skindex_offline_gives_clear_error(tmp_path: Path) -> None:
    def down(url: str) -> bytes:
        raise OSError("offline")
    lib = VantaSkinLibrary(data_dir=tmp_path, fetch=down)
    skin = next(h for h in lib.catalog() if h.id.startswith("skindex-"))
    with pytest.raises(SkinRepositoryError, match="skindex.pro"):
        lib.fetch_texture(skin.id)


def test_mojang_defaults_from_client_jar(tmp_path: Path) -> None:
    lib = VantaSkinLibrary(data_dir=tmp_path)
    # 1.6.5: hidden until the user's own client.jar exists (they could only fail to load before)
    assert not [h.id for h in lib.catalog() if h.category == "Minecraft defaults"]
    with pytest.raises(SkinRepositoryError):
        lib.fetch_texture("mojang-steve-wide")
    jar = tmp_path / "game" / "1.21.11-fabric" / "client.jar"
    jar.parent.mkdir(parents=True)
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("assets/minecraft/textures/entity/player/wide/steve.png", _png())
    ids = [h.id for h in lib.catalog() if h.category == "Minecraft defaults"]
    assert "mojang-steve-wide" in ids and "mojang-alex-slim" in ids and len(ids) == 18
    assert lib.fetch_texture("mojang-steve-wide") == _png()
