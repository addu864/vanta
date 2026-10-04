"""Repository and data directory locations."""

from __future__ import annotations

import os
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
    return repo_root() / ".vanta-data"
