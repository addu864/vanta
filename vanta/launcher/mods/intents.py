"""Planned mod names for the performance profile.

Nothing here is downloaded, installed, or marked compatible.
"""

from __future__ import annotations

PERFORMANCE_MOD_NAMES = [
    "Sodium",
    "Lithium",
    "FerriteCore",
    "ImmediatelyFast",
    "Entity Culling",
    "ModernFix",
    "Dynamic FPS",
]


def planned_mod(name: str) -> dict[str, object]:
    return {
        "name": name,
        "status": "planned, not installed",
        "installed": False,
        "compatibilityTested": False,
    }


def performance_mods() -> list[dict[str, object]]:
    return [planned_mod(name) for name in PERFORMANCE_MOD_NAMES]


def sanitize_mod_intent(raw: object) -> dict[str, object] | None:
    if isinstance(raw, str):
        name = raw.strip()
    elif isinstance(raw, dict):
        name = str(raw.get("name") or "").strip()
    else:
        return None
    if not name:
        return None
    # Status is forced. A saved file cannot claim a mod is installed or tested.
    return planned_mod(name)
