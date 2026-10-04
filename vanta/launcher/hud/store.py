"""Per-profile client UI store. One profile's layout is never copied onto another."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from vanta.launcher.hud.spec import (
    BOOL_ERROR,
    BRIGHTNESS_ERROR,
    CATEGORY_ERROR,
    DEFAULT_KEYBINDS,
    EDITOR_EMPTY_ERROR,
    EDITOR_FIELDS,
    FIELD_ERROR,
    FOV_ERROR,
    GUI_SCALE_ERROR,
    HUD_NOTE,
    KEYBIND_ACTIONS,
    KEYBIND_ACTION_ERROR,
    KEYBIND_MAP_ERROR,
    MENU_CATEGORIES,
    MODULE_ERROR,
    MODULE_FIELDS,
    MODULE_LABELS,
    MODULE_ORDER,
    PRESET_ERROR,
    PRESET_FIELDS,
    PRESET_NOTE,
    PRESET_ORDER,
    PRESETS,
    QOL_FIELDS,
    QOL_NOTE,
    SHADER_ERROR,
    VISUAL_FIELDS,
    WAYPOINT_FIELDS,
    WAYPOINT_LIMIT_ERROR,
    WAYPOINT_NAME_ERROR,
    default_record,
    parse_color,
    parse_coord,
    parse_int,
    parse_keybind,
    parse_position,
    parse_scale,
    position_ok,
    reject_unknown,
    require_bool,
    valid_waypoint_name,
)
from vanta.shared.utilities.jsonio import read_json, write_json


class ClientUiStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "client-ui.json"
        self.directory.mkdir(parents=True, exist_ok=True)

    def public_state(self, profile_name: str) -> dict[str, Any]:
        record = self._records().get(profile_name) or default_record()
        return _public(profile_name, record)

    def update_module(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        reject_unknown(payload, MODULE_FIELDS)
        module_id = str(payload.get("id") or payload.get("module") or "")
        if module_id not in MODULE_ORDER:
            raise ValueError(MODULE_ERROR)
        patch: dict[str, Any] = {}
        if "enabled" in payload:
            patch["enabled"] = require_bool(payload["enabled"])
        if "x" in payload:
            patch["x"] = parse_position(payload["x"])
        if "y" in payload:
            patch["y"] = parse_position(payload["y"])
        if "scale" in payload:
            patch["scale"] = parse_scale(payload["scale"])
        if "textColor" in payload:
            patch["textColor"] = parse_color(payload["textColor"])
        if "background" in payload:
            patch["background"] = require_bool(payload["background"])
        if not patch:
            raise ValueError(FIELD_ERROR)
        records = self._records()
        record = records.get(profile_name) or default_record()
        record["modules"][module_id].update(patch)
        records[profile_name] = record
        self._write(records)
        return _public(profile_name, record)

    def reset_layout(self, profile_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        reject_unknown(payload or {}, {"profileName", "profile"})
        records = self._records()
        record = records.get(profile_name) or default_record()
        fresh = default_record()
        record["modules"] = fresh["modules"]
        record["editor"]["selectedModule"] = "fps"
        records[profile_name] = record
        self._write(records)
        return _public(profile_name, record)

    def update_editor(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        reject_unknown(payload, EDITOR_FIELDS)
        if "category" not in payload and "selectedModule" not in payload:
            raise ValueError(EDITOR_EMPTY_ERROR)
        records = self._records()
        record = records.get(profile_name) or default_record()
        if "category" in payload:
            category = payload["category"]
            if category not in MENU_CATEGORIES:
                raise ValueError(CATEGORY_ERROR)
            record["editor"]["category"] = category
        if "selectedModule" in payload:
            selected = payload["selectedModule"]
            if selected not in MODULE_ORDER:
                raise ValueError(MODULE_ERROR)
            record["editor"]["selectedModule"] = selected
        records[profile_name] = record
        self._write(records)
        return _public(profile_name, record)

    def apply_preset(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        reject_unknown(payload, PRESET_FIELDS)
        preset = payload.get("preset")
        if preset not in PRESETS:
            raise ValueError(PRESET_ERROR)
        records = self._records()
        record = records.get(profile_name) or default_record()
        record["performance"] = {
            "preset": preset,
            "settings": copy.deepcopy(PRESETS[preset]),
        }
        records[profile_name] = record
        self._write(records)
        return _public(profile_name, record)

    def update_visual(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        reject_unknown(payload, VISUAL_FIELDS)
        if payload.get("shaders") is True:
            raise ValueError(SHADER_ERROR)
        if "shaders" in payload and payload.get("shaders") is not False:
            raise ValueError(SHADER_ERROR)
        patch: dict[str, Any] = {}
        if "fullscreen" in payload:
            patch["fullscreen"] = require_bool(payload["fullscreen"])
        if "guiScale" in payload:
            patch["guiScale"] = parse_int(payload["guiScale"], 0, 4, GUI_SCALE_ERROR)
        if "brightness" in payload:
            patch["brightness"] = parse_int(payload["brightness"], 0, 100, BRIGHTNESS_ERROR)
        if "fov" in payload:
            patch["fov"] = parse_int(payload["fov"], 30, 110, FOV_ERROR)
        if not patch and "shaders" not in payload:
            raise ValueError(FIELD_ERROR)
        records = self._records()
        record = records.get(profile_name) or default_record()
        record["visual"].update(patch)
        records[profile_name] = record
        self._write(records)
        return _public(profile_name, record)

    def update_qol(self, profile_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        reject_unknown(payload, QOL_FIELDS)
        records = self._records()
        record = records.get(profile_name) or default_record()
        qol = record["qualityOfLife"]
        changed = False
        for key in ("toggleSprint", "toggleSneak", "zoom"):
            if key in payload:
                if not isinstance(payload[key], bool):
                    raise ValueError(BOOL_ERROR)
                qol[key] = payload[key]
                changed = True
        if "waypoints" in payload:
            qol["waypoints"] = _parse_waypoints(payload["waypoints"])
            changed = True
        if "keybinds" in payload:
            qol["keybinds"] = _parse_keybinds(payload["keybinds"], qol["keybinds"])
            changed = True
        if not changed:
            raise ValueError(FIELD_ERROR)
        records[profile_name] = record
        self._write(records)
        return _public(profile_name, record)

    def _records(self) -> dict[str, dict[str, Any]]:
        data = read_json(self.path, {"profiles": {}})
        stored = data.get("profiles") if isinstance(data, dict) else {}
        if not isinstance(stored, dict):
            return {}
        records: dict[str, dict[str, Any]] = {}
        for name, raw in stored.items():
            if isinstance(name, str) and isinstance(raw, dict):
                records[name] = _normalize(raw)
        return records

    def _write(self, records: dict[str, dict[str, Any]]) -> None:
        clean = {name: _normalize(record) for name, record in records.items() if isinstance(name, str)}
        write_json(self.path, {"version": 1, "profiles": clean})


def _parse_waypoints(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise ValueError("Waypoints must be a list.")
    if len(items) > 48:
        raise ValueError(WAYPOINT_LIMIT_ERROR)
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("A waypoint needs a name and numeric x, y, and z.")
        reject_unknown(item, WAYPOINT_FIELDS)
        name = item.get("name")
        if not isinstance(name, str):
            raise ValueError(WAYPOINT_NAME_ERROR)
        name = name.strip()
        if not valid_waypoint_name(name):
            raise ValueError(WAYPOINT_NAME_ERROR)
        if name in seen:
            raise ValueError(f"A waypoint named {name} is already in this list.")
        seen.add(name)
        cleaned.append(
            {
                "name": name,
                "x": parse_coord(item.get("x")),
                "y": parse_coord(item.get("y")),
                "z": parse_coord(item.get("z")),
            }
        )
    return cleaned


def _parse_keybinds(value: Any, existing: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(KEYBIND_MAP_ERROR)
    reject_unknown(value, set(KEYBIND_ACTIONS))
    merged = dict(existing)
    for action in KEYBIND_ACTIONS:
        if action not in value:
            continue
        if action not in KEYBIND_ACTIONS:
            raise ValueError(KEYBIND_ACTION_ERROR)
        merged[action] = parse_keybind(value[action])
    for action in KEYBIND_ACTIONS:
        merged.setdefault(action, DEFAULT_KEYBINDS[action])
    return {action: merged[action] for action in KEYBIND_ACTIONS}


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    base = default_record()
    editor = raw.get("editor") if isinstance(raw.get("editor"), dict) else {}
    category = editor.get("category")
    if category in MENU_CATEGORIES:
        base["editor"]["category"] = category
    selected = editor.get("selectedModule")
    if selected in MODULE_ORDER:
        base["editor"]["selectedModule"] = selected
    modules = raw.get("modules") if isinstance(raw.get("modules"), dict) else {}
    for module_id in MODULE_ORDER:
        item = modules.get(module_id)
        if not isinstance(item, dict):
            continue
        current = base["modules"][module_id]
        if isinstance(item.get("enabled"), bool):
            current["enabled"] = item["enabled"]
        if position_ok(item.get("x")):
            current["x"] = parse_position(item["x"])
        if position_ok(item.get("y")):
            current["y"] = parse_position(item["y"])
        try:
            current["scale"] = parse_scale(item.get("scale"))
        except ValueError:
            pass
        try:
            current["textColor"] = parse_color(item.get("textColor"))
        except ValueError:
            pass
        if isinstance(item.get("background"), bool):
            current["background"] = item["background"]
    performance = raw.get("performance") if isinstance(raw.get("performance"), dict) else {}
    preset = performance.get("preset")
    if preset in PRESETS:
        base["performance"] = {"preset": preset, "settings": copy.deepcopy(PRESETS[preset])}
    visual = raw.get("visual") if isinstance(raw.get("visual"), dict) else {}
    if isinstance(visual.get("fullscreen"), bool):
        base["visual"]["fullscreen"] = visual["fullscreen"]
    for key, low, high in (("guiScale", 0, 4), ("brightness", 0, 100), ("fov", 30, 110)):
        value = visual.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
            base["visual"][key] = value
    qol = raw.get("qualityOfLife") if isinstance(raw.get("qualityOfLife"), dict) else {}
    for key in ("toggleSprint", "toggleSneak", "zoom"):
        if isinstance(qol.get(key), bool):
            base["qualityOfLife"][key] = qol[key]
    try:
        if isinstance(qol.get("waypoints"), list):
            base["qualityOfLife"]["waypoints"] = _parse_waypoints(qol["waypoints"])
    except ValueError:
        base["qualityOfLife"]["waypoints"] = []
    binds = qol.get("keybinds") if isinstance(qol.get("keybinds"), dict) else {}
    cleaned_binds = dict(DEFAULT_KEYBINDS)
    for action in KEYBIND_ACTIONS:
        if action in binds:
            try:
                cleaned_binds[action] = parse_keybind(binds[action])
            except ValueError:
                pass
    base["qualityOfLife"]["keybinds"] = cleaned_binds
    return base


def _public(profile_name: str, record: dict[str, Any]) -> dict[str, Any]:
    modules = []
    for module_id in MODULE_ORDER:
        item = record["modules"][module_id]
        modules.append(
            {
                "id": module_id,
                "label": MODULE_LABELS[module_id],
                "enabled": item["enabled"],
                "x": item["x"],
                "y": item["y"],
                "scale": item["scale"],
                "textColor": item["textColor"],
                "background": item["background"],
            }
        )
    performance = record["performance"]
    visual = dict(record["visual"])
    visual["shaders"] = False
    visual["shaderPacks"] = "none"
    visual["appliedToGame"] = False
    qol = {
        "toggleSprint": record["qualityOfLife"]["toggleSprint"],
        "toggleSneak": record["qualityOfLife"]["toggleSneak"],
        "zoom": record["qualityOfLife"]["zoom"],
        "waypoints": copy.deepcopy(record["qualityOfLife"]["waypoints"]),
        "keybinds": dict(record["qualityOfLife"]["keybinds"]),
        "appliedInGame": False,
        "note": QOL_NOTE,
    }
    return {
        "profileName": profile_name,
        "renderedInGame": False,
        "minecraftHudDrawn": False,
        "note": HUD_NOTE,
        "categories": list(MENU_CATEGORIES),
        "editor": {
            "category": record["editor"]["category"],
            "selectedModule": record["editor"]["selectedModule"],
        },
        "modules": modules,
        "performance": {
            "preset": performance["preset"],
            "settings": copy.deepcopy(performance["settings"]),
            "availablePresets": list(PRESET_ORDER),
            "appliedToGame": False,
            "fpsGainClaimed": False,
            "downloadsMods": False,
            "note": PRESET_NOTE,
        },
        "visual": visual,
        "qualityOfLife": qol,
    }
