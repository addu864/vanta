"""Original Vanta controller labels.

These names are labels only. They are not sent to Minecraft, and they were
not checked in a running game.
"""

from __future__ import annotations

from typing import Any

# Face button, shoulder, stick, and menu ids. Not a copy of another client's map.
BUTTONS: tuple[dict[str, str], ...] = (
    {"id": "a", "face": "A", "action": "jump"},
    {"id": "b", "face": "B", "action": "sneak"},
    {"id": "x", "face": "X", "action": "inventory"},
    {"id": "y", "face": "Y", "action": "drop"},
    {"id": "lb", "face": "LB", "action": "hotbar-previous"},
    {"id": "rb", "face": "RB", "action": "hotbar-next"},
    {"id": "lt", "face": "LT", "action": "use"},
    {"id": "rt", "face": "RT", "action": "attack"},
    {"id": "start", "face": "Start", "action": "pause"},
    {"id": "back", "face": "Back", "action": "menu"},
    {"id": "left-stick", "face": "Left stick", "action": "move"},
    {"id": "right-stick", "face": "Right stick", "action": "look"},
)

ACTIONS: tuple[dict[str, str], ...] = (
    {"id": "jump", "label": "Jump"},
    {"id": "sneak", "label": "Sneak"},
    {"id": "inventory", "label": "Inventory"},
    {"id": "drop", "label": "Drop"},
    {"id": "hotbar-previous", "label": "Hotbar previous"},
    {"id": "hotbar-next", "label": "Hotbar next"},
    {"id": "use", "label": "Use"},
    {"id": "attack", "label": "Attack"},
    {"id": "pause", "label": "Pause"},
    {"id": "menu", "label": "Menu"},
    {"id": "move", "label": "Move"},
    {"id": "look", "label": "Look"},
)

BUTTON_IDS = frozenset(item["id"] for item in BUTTONS)
ACTION_IDS = frozenset(item["id"] for item in ACTIONS)
ACTION_LABELS = {item["id"]: item["label"] for item in ACTIONS}
BUTTON_FACES = {item["id"]: item["face"] for item in BUTTONS}
DEFAULT_ACTIONS = {item["id"]: item["action"] for item in BUTTONS}
DEFAULT_DEADZONE = 0.15


def button_rows(actions: dict[str, str]) -> list[dict[str, Any]]:
    rows = []
    for spec in BUTTONS:
        action = actions.get(spec["id"], spec["action"])
        rows.append(
            {
                "id": spec["id"],
                "face": spec["face"],
                "action": action,
                "actionLabel": ACTION_LABELS.get(action, action),
            }
        )
    return rows
