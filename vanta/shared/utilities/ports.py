"""Localhost port selection."""

from __future__ import annotations

import socket

PREFERRED_PORT = 8765


def choose_port(host: str = "127.0.0.1", preferred: int = PREFERRED_PORT) -> int:
    """Use the preferred port when it is free, otherwise any free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, preferred))
        except OSError:
            sock.bind((host, 0))
        return int(sock.getsockname()[1])
