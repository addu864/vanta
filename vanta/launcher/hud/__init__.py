"""Per-profile HUD layout and in-game menu spec.

The layout is stored for a future client mod. Vanta does not draw it in Minecraft.
"""

from vanta.launcher.hud.spec import HUD_NOTE, MENU_CATEGORIES, MODULE_ORDER
from vanta.launcher.hud.store import ClientUiStore

__all__ = ["ClientUiStore", "HUD_NOTE", "MENU_CATEGORIES", "MODULE_ORDER"]
