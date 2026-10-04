"""HUD modules, menu categories, and performance preset bundles.

Presets are stored preferences. They are not a frame-rate measurement and they
do not download mods.
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any

HUD_NOTE = (
    "HUD layout is saved per profile and would be read by a future client mod. "
    "It was not shown in Minecraft."
)
PRESET_NOTE = (
    "Stored settings bundle only. Vanta does not measure or promise a frame rate, "
    "and it does not download performance mods."
)
QOL_NOTE = (
    "Toggle sprint, toggle sneak, zoom, waypoints, and keybinds are stored on the profile. "
    "They are not applied inside a running game."
)

MENU_CATEGORIES = ("PERFORMANCE", "VISUAL", "HUD", "QUALITY OF LIFE")

# id, label, enabled, x, y
_MODULE_ROWS: tuple[tuple[str, str, bool, int, int], ...] = (
    ("fps", "FPS", True, 8, 8),
    ("ping", "Ping", True, 8, 28),
    ("coordinates", "Coordinates", True, 8, 48),
    ("cps", "CPS", False, 8, 68),
    ("keystrokes", "Keystrokes", False, 8, 96),
    ("ram", "RAM", False, 8, 160),
    ("sprint", "Sprint", False, 8, 180),
    ("sneak", "Sneak", False, 8, 200),
    ("armor", "Armor / status", False, 8, 220),
)

MODULE_ORDER: tuple[str, ...] = tuple(row[0] for row in _MODULE_ROWS)
MODULE_LABELS: dict[str, str] = {row[0]: row[1] for row in _MODULE_ROWS}

SCALE_MIN = 0.5
SCALE_MAX = 3.0
POSITION_MIN = -8192
POSITION_MAX = 8192
COORD_LIMIT = 30_000_000
MAX_WAYPOINTS = 48

SCALE_ERROR = "Scale must be a number from 0.5 to 3."
POSITION_ERROR = "Position must be a number from -8192 to 8192."
MODULE_ERROR = "Unknown HUD module."
PRESET_ERROR = "Unknown performance preset. Choose MAX FPS, BALANCED, PRETTY, or CINEMATIC."
CATEGORY_ERROR = "Unknown menu category."
FORBIDDEN_ERROR = "Vanta does not include that option."
KEYBIND_ERROR = "A keybind must be a short name made of letters, digits, or underscores."
WAYPOINT_ERROR = "A waypoint needs a name and numeric x, y, and z."
SHADER_ERROR = "Vanta does not enable or download shader packs."
FIELD_ERROR = "That field is not part of the HUD editor."
COLOR_ERROR = "Text color must be a #RRGGBB value."
BOOL_ERROR = "That option must be true or false."
GUI_SCALE_ERROR = "GUI scale must be an integer from 0 to 4."
BRIGHTNESS_ERROR = "Brightness must be an integer from 0 to 100."
FOV_ERROR = "FOV must be an integer from 30 to 110."
KEYBIND_MAP_ERROR = "Keybinds must be a map of action to key."
KEYBIND_ACTION_ERROR = "Unknown keybind action."
WAYPOINT_NAME_ERROR = "Waypoint name must be 1–32 printable characters."
WAYPOINT_LIMIT_ERROR = "A profile can store at most 48 waypoints."
EDITOR_EMPTY_ERROR = "Choose a menu category or a HUD module."

PRESET_ORDER = ("MAX FPS", "BALANCED", "PRETTY", "CINEMATIC")

# Preference bundles only. fpsCapPreference is a stored label, not a measured rate.
PRESETS: dict[str, dict[str, Any]] = {
    "MAX FPS": {
        "renderDistance": 8,
        "graphics": "fast",
        "clouds": "off",
        "particles": "minimal",
        "smoothLighting": False,
        "entityShadows": False,
        "vsync": False,
        "biomeBlend": 0,
        "fpsCapPreference": "unlimited",
    },
    "BALANCED": {
        "renderDistance": 12,
        "graphics": "fast",
        "clouds": "fast",
        "particles": "decreased",
        "smoothLighting": True,
        "entityShadows": True,
        "vsync": False,
        "biomeBlend": 2,
        "fpsCapPreference": "unlimited",
    },
    "PRETTY": {
        "renderDistance": 16,
        "graphics": "fancy",
        "clouds": "fancy",
        "particles": "all",
        "smoothLighting": True,
        "entityShadows": True,
        "vsync": False,
        "biomeBlend": 5,
        "fpsCapPreference": "unlimited",
    },
    "CINEMATIC": {
        "renderDistance": 24,
        "graphics": "fancy",
        "clouds": "fancy",
        "particles": "all",
        "smoothLighting": True,
        "entityShadows": True,
        "vsync": True,
        "biomeBlend": 7,
        "fpsCapPreference": "60",
    },
}

KEYBIND_ACTIONS = ("toggleSprint", "toggleSneak", "zoom")
DEFAULT_KEYBINDS = {
    "toggleSprint": "R",
    "toggleSneak": "LEFT_SHIFT",
    "zoom": "C",
}

DEFAULT_TEXT_COLOR = "#E7EEE9"
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_KEYBIND = re.compile(r"^[A-Za-z0-9_]{1,24}$")
_FORBIDDEN = {"freecam", "killaura", "reach", "xray"}

MODULE_FIELDS = {"profileName", "profile", "id", "module", "enabled", "x", "y", "scale", "textColor", "background"}
EDITOR_FIELDS = {"profileName", "profile", "category", "selectedModule"}
PRESET_FIELDS = {"profileName", "profile", "preset"}
VISUAL_FIELDS = {"profileName", "profile", "fullscreen", "guiScale", "brightness", "fov", "shaders"}
QOL_FIELDS = {"profileName", "profile", "toggleSprint", "toggleSneak", "zoom", "waypoints", "keybinds"}
WAYPOINT_FIELDS = {"name", "x", "y", "z"}


def default_modules() -> dict[str, dict[str, Any]]:
    modules: dict[str, dict[str, Any]] = {}
    for module_id, _label, enabled, x, y in _MODULE_ROWS:
        modules[module_id] = {
            "enabled": enabled,
            "x": x,
            "y": y,
            "scale": 1.0,
            "textColor": DEFAULT_TEXT_COLOR,
            "background": False,
        }
    return modules


def default_record() -> dict[str, Any]:
    return {
        "editor": {"category": "HUD", "selectedModule": "fps"},
        "modules": default_modules(),
        "performance": {
            "preset": "BALANCED",
            "settings": copy.deepcopy(PRESETS["BALANCED"]),
        },
        "visual": {
            "fullscreen": False,
            "guiScale": 2,
            "brightness": 50,
            "fov": 70,
        },
        "qualityOfLife": {
            "toggleSprint": False,
            "toggleSneak": False,
            "zoom": False,
            "waypoints": [],
            "keybinds": dict(DEFAULT_KEYBINDS),
        },
    }


def is_forbidden_key(key: str) -> bool:
    folded = "".join(ch for ch in str(key).lower() if ch.isalnum())
    return folded in _FORBIDDEN


def reject_forbidden(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if is_forbidden_key(str(key)):
                raise ValueError(FORBIDDEN_ERROR)
            reject_forbidden(item)
    elif isinstance(value, list):
        for item in value:
            reject_forbidden(item)


def reject_unknown(payload: dict[str, Any], allowed: set[str]) -> None:
    if not isinstance(payload, dict):
        raise ValueError(FIELD_ERROR)
    for key in payload:
        if key not in allowed:
            if is_forbidden_key(str(key)):
                raise ValueError(FORBIDDEN_ERROR)
            raise ValueError(FIELD_ERROR)
    reject_forbidden(payload)


def require_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(BOOL_ERROR)
    return value


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(SCALE_ERROR)
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(SCALE_ERROR)
    return number


def parse_scale(value: Any) -> float:
    number = _finite_number(value)
    if number < SCALE_MIN or number > SCALE_MAX:
        raise ValueError(SCALE_ERROR)
    return round(number, 4)


def parse_position(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(POSITION_ERROR)
    number = float(value)
    if not math.isfinite(number) or number < POSITION_MIN or number > POSITION_MAX:
        raise ValueError(POSITION_ERROR)
    if number == math.trunc(number):
        return int(number)
    return round(number, 2)


def position_ok(value: Any) -> bool:
    try:
        parse_position(value)
    except ValueError:
        return False
    return True


def parse_color(value: Any) -> str:
    if not isinstance(value, str) or not _COLOR.fullmatch(value):
        raise ValueError(COLOR_ERROR)
    return value.upper()


def parse_int(value: Any, low: int, high: int, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(message)
    if value < low or value > high:
        raise ValueError(message)
    return value


def parse_coord(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(WAYPOINT_ERROR)
    number = float(value)
    if not math.isfinite(number) or abs(number) > COORD_LIMIT:
        raise ValueError(WAYPOINT_ERROR)
    if number == math.trunc(number):
        return int(number)
    return round(number, 3)


def valid_waypoint_name(name: str) -> bool:
    if not 1 <= len(name) <= 32:
        return False
    return all(ch.isprintable() and ch not in "\r\n\t" for ch in name)


def parse_keybind(value: Any) -> str:
    if not isinstance(value, str) or not _KEYBIND.fullmatch(value):
        raise ValueError(KEYBIND_ERROR)
    return value.upper()
