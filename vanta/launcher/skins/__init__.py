"""Skin browser for Vanta.

The UI talks to VantaApp, which talks to a SkinRepository.
The default library is stored in the app. Ely.by is not used.
"""

from vanta.launcher.skins.builtin import VantaSkinLibrary
from vanta.launcher.skins.manager import SkinManager
from vanta.launcher.skins.models import LOCAL_APPLY_NOTE, SkinHit
from vanta.launcher.skins.repository import SkinRepository, SkinRepositoryError

__all__ = [
    "LOCAL_APPLY_NOTE",
    "SkinHit",
    "SkinManager",
    "SkinRepository",
    "SkinRepositoryError",
    "VantaSkinLibrary",
]
