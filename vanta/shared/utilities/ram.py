"""RAM bounds for Vanta 0.1."""

from __future__ import annotations

RAM_ERROR = "RAM must be an integer from 2 to 8 GB."
RAM_MIN = 2
RAM_MAX = 8


def parse_ram(value: object) -> tuple[bool, int | None, str]:
    if isinstance(value, bool):
        return False, None, RAM_ERROR
    number: int | None = None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    if number is None or number < RAM_MIN or number > RAM_MAX:
        return False, None, RAM_ERROR
    return True, number, ""
