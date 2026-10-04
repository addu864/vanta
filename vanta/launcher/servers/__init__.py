"""Local Minecraft server manager (Vanta 0.4) and optional tunnel (Vanta 0.5).

Servers run as separate processes. This package does not download or bundle
Paper, Fabric, Mojang server software, or a tunnel binary.
"""

from vanta.launcher.servers.manager import JAR_MISSING, NOT_PAPER, ServerManager

__all__ = ["JAR_MISSING", "NOT_PAPER", "ServerManager"]
