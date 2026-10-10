"""Repository and data directory locations."""

from __future__ import annotations

import os
import platform
from pathlib import Path


def repo_root() -> Path:
    """Directory that contains the vanta package, assets, and docs."""
    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "vanta" / "__main__.py").is_file() and (candidate / "assets").is_dir():
            return candidate
    return Path(__file__).resolve().parents[3]


def default_data_dir() -> Path:
    """Config lives inside the repo so tests and the local UI share one place.

    VANTA_DATA_DIR overrides the location. Tokens are never written here.
    """
    override = os.environ.get("VANTA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    root = repo_root()
    if mac_app_bundle(root):
        # A .app may be read-only or translocated by Gatekeeper; keep data outside it.
        return mac_support_dir()
    return root / ".vanta-data"


def mac_app_bundle(root: Path) -> bool:
    return platform.system() == "Darwin" and ".app/Contents/" in (str(root).replace("\\", "/") + "/")


def mac_support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Vanta"
