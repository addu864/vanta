"""Human-readable file log plus the last error string."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock


class VantaLog:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "vanta.log"
        self._error_path = directory / "last-error.txt"
        self._lock = Lock()
        if self._error_path.is_file():
            self.last_error = self._error_path.read_text(encoding="utf-8")
        else:
            self.last_error = ""

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def error(self, message: str) -> None:
        with self._lock:
            self.last_error = message
            self._error_path.write_text(message, encoding="utf-8")
        self._write("ERROR", message)

    def clear_error(self) -> None:
        with self._lock:
            self.last_error = ""
            self._error_path.write_text("", encoding="utf-8")

    def tail(self, limit: int = 200) -> list[str]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return lines[-limit:]

    def _write(self, level: str, message: str) -> None:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"{stamp} {level} {message}\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
