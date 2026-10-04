"""Validate Minecraft skin PNG dimensions without an image library."""

from __future__ import annotations

PNG_SIG = b"\x89PNG\r\n\x1a\n"
ALLOWED_SIZES = {(64, 64), (64, 32)}
MAX_SKIN_BYTES = 256 * 1024


def inspect_skin_png(data: bytes) -> tuple[int, int]:
    """Return (width, height) or raise ValueError with a user-facing message."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("That file is not a PNG skin.")
    blob = bytes(data)
    if len(blob) > MAX_SKIN_BYTES:
        raise ValueError("That skin file is too large.")
    if not blob.startswith(PNG_SIG) or len(blob) < 24:
        raise ValueError("That file is not a PNG skin.")
    length = int.from_bytes(blob[8:12], "big")
    chunk_type = blob[12:16]
    if chunk_type != b"IHDR" or length != 13 or len(blob) < 24:
        raise ValueError("That file is not a valid PNG skin.")
    width = int.from_bytes(blob[16:20], "big")
    height = int.from_bytes(blob[20:24], "big")
    if (width, height) not in ALLOWED_SIZES:
        raise ValueError(
            f"Skin PNG must be 64x64 or 64x32. This file is {width}x{height}."
        )
    return width, height
