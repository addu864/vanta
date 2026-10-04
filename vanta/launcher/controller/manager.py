"""Save controller labels, deadzone, and launcher navigation per profile."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from vanta.launcher.controller.catalog import (
    ACTION_IDS,
    ACTION_LABELS,
    ACTIONS,
    BUTTON_IDS,
    DEFAULT_ACTIONS,
    DEFAULT_DEADZONE,
    button_rows,
)
from vanta.launcher.controller.detect import STATUS_NO_DEVICE, detect_controllers
from vanta.launcher.mods.manager import profile_slug
from vanta.shared.utilities.guard import contains_token_key
from vanta.shared.utilities.jsonio import read_json, write_json

BOOL_ERROR = "That option must be true or false."
DEADZONE_ERROR = "Stick deadzone must be a number from 0 to 1."
BUTTON_ERROR = "Unknown button. Use a button from the Vanta map."
ACTION_ERROR = "Unknown action label. Pick one of the Vanta labels."
FIELD_ERROR = "That field is not part of the controller settings."
TOKEN_ERROR = "Vanta 0.1 does not accept or store tokens."

_ALLOWED = {"profilename", "profile", "deadzone", "launchernavigation", "buttons"}


class ControllerManager:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)

    def manifest_path(self, profile_name: str) -> Path:
        return self.data_dir / "profiles" / profile_slug(profile_name) / "controller.json"

    def state(self, profile_name: str) -> dict[str, Any]:
        return self._public(profile_name, self._read(profile_name), ok=True)

    def update(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._reject(payload)
        stored = self._read(profile_name)
        if "deadzone" in payload:
            stored["deadzone"] = _parse_deadzone(payload.get("deadzone"))
        if "launcherNavigation" in payload:
            stored["launcherNavigation"] = _parse_flag(payload.get("launcherNavigation"))
        if "buttons" in payload:
            changes = _parse_buttons(payload.get("buttons"))
            stored["buttons"].update(changes)
        if any(key in payload for key in ("deadzone", "launcherNavigation", "buttons")):
            self._write(profile_name, stored)
        return self._public(profile_name, stored, ok=True)

    def _public(self, profile_name: str, stored: dict[str, Any], *, ok: bool) -> dict[str, Any]:
        detection = detect_controllers()
        buttons = stored["buttons"]
        body = {
            "ok": ok,
            "profileName": profile_name,
            "status": detection["status"],
            "deviceFound": detection["deviceFound"] is True and detection["status"] != STATUS_NO_DEVICE,
            "devices": detection["devices"],
            "checks": detection["checks"],
            "checkSummary": detection["checkSummary"],
            "deadzone": stored["deadzone"],
            "launcherNavigation": stored["launcherNavigation"] is True,
            "buttons": button_rows(buttons),
            "actions": [{"id": item["id"], "label": item["label"]} for item in ACTIONS],
            "labelsOnly": True,
            "appliedInGame": False,
            "testedInMinecraft": False,
            "inGameControllerPlayTested": False,
            "minecraftLaunched": False,
        }
        return body

    def _read(self, profile_name: str) -> dict[str, Any]:
        path = self.manifest_path(profile_name)
        data = read_json(path, {}) if path.is_file() else {}
        if not isinstance(data, dict):
            data = {}
        buttons = dict(DEFAULT_ACTIONS)
        raw_buttons = data.get("buttons")
        if isinstance(raw_buttons, dict):
            for key, action in raw_buttons.items():
                if not isinstance(key, str) or not isinstance(action, str):
                    continue
                button_id = key.strip().lower()
                action_id = action.strip().lower()
                if button_id in BUTTON_IDS and action_id in ACTION_IDS:
                    buttons[button_id] = action_id
        return {
            "deadzone": _stored_deadzone(data.get("deadzone")),
            "launcherNavigation": data.get("launcherNavigation") is True,
            "buttons": buttons,
        }

    def _write(self, profile_name: str, stored: dict[str, Any]) -> None:
        write_json(
            self.manifest_path(profile_name),
            {
                "version": 1,
                "deadzone": stored["deadzone"],
                "launcherNavigation": stored["launcherNavigation"] is True,
                "buttons": {button_id: stored["buttons"][button_id] for button_id in DEFAULT_ACTIONS},
            },
        )

    def _reject(self, payload: dict[str, Any]) -> None:
        if contains_token_key(payload):
            raise ValueError(TOKEN_ERROR)
        for key in payload:
            normalized = str(key).strip().lower().replace("_", "").replace("-", "")
            if normalized not in _ALLOWED:
                raise ValueError(FIELD_ERROR)


def _parse_deadzone(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(DEADZONE_ERROR)
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 1:
        raise ValueError(DEADZONE_ERROR)
    return number


def _stored_deadzone(value: Any) -> float:
    try:
        return _parse_deadzone(value)
    except ValueError:
        return DEFAULT_DEADZONE


def _parse_flag(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(BOOL_ERROR)
    return value


def _parse_buttons(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(BUTTON_ERROR)
    cleaned: dict[str, str] = {}
    for key, action in value.items():
        if not isinstance(key, str):
            raise ValueError(BUTTON_ERROR)
        button_id = key.strip().lower()
        if button_id not in BUTTON_IDS:
            raise ValueError(BUTTON_ERROR)
        if not isinstance(action, str):
            raise ValueError(ACTION_ERROR)
        action_id = action.strip().lower()
        if action_id not in ACTION_LABELS:
            raise ValueError(ACTION_ERROR)
        cleaned[button_id] = action_id
    return cleaned
