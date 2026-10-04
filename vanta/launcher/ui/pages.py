"""HTML shell for the local UI. Version options are not hard-coded here."""

from __future__ import annotations

from pathlib import Path


def index_page() -> str:
    return Path(__file__).with_name("index.html").read_text(encoding="utf-8")
