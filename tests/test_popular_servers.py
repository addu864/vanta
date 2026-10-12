"""Popular servers bar: config, status ping parsing, servers.dat, join args."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from vanta.launcher.servers import popular


def test_default_list_is_seeded_and_editable(tmp_path: Path) -> None:
    servers = popular.load_servers(tmp_path)
    hosts = [s["host"] for s in servers]
    assert {"bluemc.org", "rulexmc.mcsh.io", "alwto.net", "play.pika-network.net", "mc.fatalmc.org"} <= set(hosts)
    path = tmp_path / popular.CONFIG_NAME
    path.write_text(json.dumps({"servers": [{"name": "Mine", "host": "example.org", "port": 25570}, {"name": "bad"}]}))
    servers = popular.load_servers(tmp_path)
    assert len(servers) == 1 and servers[0]["port"] == 25570 and popular.address(servers[0]) == "example.org:25570"


def test_status_parsing_strips_colour_codes() -> None:
    payload = {"version": {"name": "§fBlue Proxy"}, "players": {"online": 12, "max": 250},
               "description": {"text": "§aHello ", "extra": [{"text": "§bWorld"}, "\n§cLine 2"]}}
    out = popular.parse_status(payload)
    assert out == {"online": True, "players": 12, "maxPlayers": 250, "version": "Blue Proxy", "motd": "Hello World\nLine 2"}


def test_status_uses_srv_then_default_port_and_caches() -> None:
    popular._cache.clear()
    tried = []

    def pinger(host, port):
        tried.append((host, port))
        if port == 25565:
            raise OSError("refused")
        return {"online": True, "players": 1, "maxPlayers": 2, "version": "", "motd": "hi", "latencyMs": 5}

    server = {"id": "x", "name": "X", "host": "x.example", "port": None}
    out = popular.server_status(server, pinger=pinger, srv=lambda h: ("node.x.example", 25599))
    assert out["online"] and out["resolved"] == "node.x.example:25599" and tried == [("node.x.example", 25599)]
    again = popular.server_status(server, pinger=pinger, srv=lambda h: None)
    assert again["cached"] is True and len(tried) == 1
    popular._cache.clear()
    down = popular.server_status({"id": "y", "name": "Y", "host": "y.example", "port": None}, pinger=pinger, srv=lambda h: None)
    assert down["online"] is False and "refused" in down["error"]


def test_handshake_packet_shape() -> None:
    raw = popular.status_packets("a.b", 25565)
    assert raw.endswith(b"\x01\x00")
    assert struct.pack(">H", 25565) in raw and b"a.b" in raw


def test_servers_dat_append_keeps_existing_entries(tmp_path: Path) -> None:
    path = tmp_path / "servers.dat"
    assert popular.add_to_server_list(path, "Old", "old.example") is True
    assert popular.add_to_server_list(path, "PikaNetwork", "play.pika-network.net") is True
    assert popular.add_to_server_list(path, "PikaNetwork", "PLAY.pika-network.net") is False
    assert popular.read_server_list(path) == [{"name": "Old", "ip": "old.example"}, {"name": "PikaNetwork", "ip": "play.pika-network.net"}]
    assert (tmp_path / "servers.dat_old").is_file()


def test_join_arguments_by_version() -> None:
    assert popular.join_arguments("1.20.1", "a.b", None) == ["--quickPlayMultiplayer", "a.b"]
    assert popular.join_arguments("26.3", "a.b", 25570) == ["--quickPlayMultiplayer", "a.b:25570"]
    assert popular.join_arguments("1.19.4", "a.b", None) == ["--server", "a.b", "--port", "25565"]
