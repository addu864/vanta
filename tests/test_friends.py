import json
import socket
import time

import pytest

from vanta.launcher.friends import codes, lan
from vanta.launcher.friends.codes import CodeError
from vanta.launcher.friends.service import FriendsService, find_lan_port, find_shared_address
from vanta.launcher.friends.store import FriendStore


# ---- friend codes ----
def test_friend_code_roundtrip_and_link():
    ident = codes.new_id()
    code = codes.friend_code("itz_alone10", ident)
    assert codes.parse_friend_code(code)["id"] == ident
    assert codes.parse_friend_code(codes.friend_link(code)) == codes.parse_friend_code(code)
    assert codes.parse_friend_code("  " + code.lower().replace("itz_alone10", "itz_alone10") + " ")["id"] == ident


def test_friend_code_rejects_typos_and_junk():
    code = codes.friend_code("Steve", "ABCDEFGH23")
    bad = code[:-4] + ("Z" if code[-4] != "Z" else "Y") + code[-3:]
    for text in (bad, "", "hello", "Steve-ABC-12", "x" * 30 + "-ABCDEFGH23-AA"):
        with pytest.raises(CodeError):
            codes.parse_friend_code(text)
    with pytest.raises(CodeError):
        codes.friend_code("bad name!", "ABCDEFGH23")


# ---- world invites ----
def test_invite_roundtrip():
    inv = codes.make_invite("Fox-Hat.EU.e4mc.link", "1.21.11", "itz_alone10", now=1000)
    assert inv.startswith("VW1.") and "=" not in inv
    out = codes.parse_invite(inv, now=1001)
    assert out == {"host": "fox-hat.eu.e4mc.link", "port": None, "address": "fox-hat.eu.e4mc.link", "version": "1.21.11",
                   "hostName": "itz_alone10", "loader": "fabric", "expires": 1000 + codes.INVITE_TTL_S}
    assert codes.parse_invite("vanta://join/" + inv, now=1001)["host"] == "fox-hat.eu.e4mc.link"
    assert codes.parse_invite(codes.make_invite("192.168.1.5:51234", "1.20.1", "a", now=0), now=1)["port"] == 51234


def test_invite_expiry_and_damage():
    inv = codes.make_invite("a.e4mc.link", "1.21.11", "Bob", ttl_s=60, now=0)
    with pytest.raises(CodeError, match="expired"):
        codes.parse_invite(inv, now=61)
    import base64
    def mk(obj):
        return "VW1." + base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    for text in ("VW1.!!!!", "VW2.abc", "nope", mk([1]), mk({"a": "evil host;rm", "v": "1.21"}),
                 mk({"a": "x.e4mc.link:99999", "v": "1.21"}), mk({"a": "x.e4mc.link", "v": ""})):
        with pytest.raises(CodeError):
            codes.parse_invite(text, now=0)
    weird = codes.parse_invite(mk({"a": "x.e4mc.link", "v": "1.21", "h": "<b>Bo b</b>", "l": "forge"}), now=0)
    assert weird["hostName"] == "bBobb" and weird["loader"] == "fabric"


# ---- friend list ----
def test_store_identity_add_remove(tmp_path):
    s = FriendStore(tmp_path)
    me = s.identity("itz_alone10")
    assert me == s.identity("itz_alone10")  # stable id
    assert (tmp_path / "friends" / "friends.json").exists()
    assert s.identity("bad name")["name"] == "Player"
    other = codes.friend_code("Alex", "QRSTVWXYZ2")
    assert s.add(other, my_id=me["id"])["already"] is False
    assert s.add(codes.friend_link(codes.friend_code("Alex2", "QRSTVWXYZ2")), my_id=me["id"])["already"] is True
    assert [f["name"] for f in s.friends()] == ["Alex2"]  # renamed, not duplicated
    with pytest.raises(CodeError, match="own"):
        s.add(me["code"], my_id=me["id"])
    assert FriendStore(tmp_path).friends()[0]["id"] == "QRSTVWXYZ2"  # persisted
    assert s.remove("QRSTVWXYZ2") and not s.remove("QRSTVWXYZ2") and s.friends() == []


# ---- LAN protocol ----
def test_lan_decode_is_strict():
    assert lan.decode(b"not json") is None
    assert lan.decode(json.dumps({"t": "other", "id": "X"}).encode()) is None
    assert lan.decode(b"x" * 2000) is None
    m = lan.decode(lan.encode({"id": "abcdefghjk", "name": "N" * 40, "port": 70000, "knows": ["a"] * 100}))
    assert m["id"] == "ABCDEFGHJK" and len(m["name"]) == 16 and m["port"] is None and len(m["knows"]) == 64


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return False


def test_lan_two_installs_see_each_other_on_loopback(tmp_path):
    port = _free_udp_port()
    target = [("127.0.0.1", port)]
    nodes = []
    for name in ("itz_alone10", "Friend"):
        holder = {}
        node = lan.LanPresence(lambda h=holder: h["svc"]._beacon(), port=port, targets=target, interval_s=0.1, bind_host="127.0.0.1")
        svc = FriendsService(tmp_path / name, lan=node, name=lambda n=name: n)
        holder["svc"] = svc
        nodes.append((svc, node))
    (a, la), (b, lb) = nodes
    try:
        assert la.start() and lb.start()
        # both bound to the same port with SO_REUSEADDR/PORT; each sends to it
        assert _wait(lambda: a.overview()["nearby"] or b.overview()["nearby"])
        # A adds B; B hosts a LAN world
        a.add(b.me()["code"])
        b.set_presence(playing=True, mc="1.21.11", world="Island", port=51234)
        b.add(a.me()["code"])
        ok = _wait(lambda: (lambda f: f and f[0].get("join") and f[0]["mutual"])(a.overview()["friends"]))
        if not ok:  # some kernels deliver unicast to only one of two sockets sharing a port
            raw = lan.encode(b._beacon())
            la.handle(raw, "127.0.0.1", a.me()["id"])
        f = a.overview()["friends"][0]
        assert f["online"] and f["name"] == "Friend" and f["mc"] == "1.21.11" and f["world"] == "Island"
        assert a.lan_join_target(f["id"]) == {"host": "127.0.0.1", "port": 51234, "version": "1.21.11", "name": "Friend"}
    finally:
        la.stop(); lb.stop()


def test_lan_dedicated_sockets_loopback(tmp_path):
    """Strict check: a real UDP packet from one node reaches another bound on its own port."""
    pa, pb = _free_udp_port(), _free_udp_port()
    beacon_a = {"id": "AAAAAAAAAA", "name": "A", "code": "A-AAAAAAAAAA-XX", "status": "online"}
    na = lan.LanPresence(lambda: beacon_a, port=pa, targets=[("127.0.0.1", pb)], interval_s=0.1, bind_host="127.0.0.1")
    nb = lan.LanPresence(lambda: {"id": "BBBBBBBBBB"}, port=pb, targets=[("127.0.0.1", pa)], interval_s=0.1, bind_host="127.0.0.1")
    try:
        assert na.start() and nb.start()
        assert _wait(lambda: "AAAAAAAAAA" in nb.online() and "BBBBBBBBBB" in na.online())
        assert nb.online()["AAAAAAAAAA"]["ip"] == "127.0.0.1"
    finally:
        na.stop(); nb.stop()


def test_lan_peer_times_out_and_busy_port_is_graceful():
    t = [0.0]
    node = lan.LanPresence(lambda: {"id": "MEMEMEMEME"}, port=0, targets=[], clock=lambda: t[0])
    node.handle(lan.encode({"id": "PEERPEERPE", "name": "P"}), "10.0.0.2", "MEMEMEMEME")
    node.handle(lan.encode({"id": "MEMEMEMEME"}), "10.0.0.3", "MEMEMEMEME")  # own echo ignored
    assert list(node.online()) == ["PEERPEERPE"]
    t[0] = lan.TIMEOUT_S + 1
    assert node.online() == {}
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    busy = lan.LanPresence(lambda: {"id": "X"}, port=blocker.getsockname()[1], targets=[], bind_host="127.0.0.1")
    busy._open = lambda: (_ for _ in ()).throw(OSError(10013, "access denied"))
    assert busy.start() is False and "Friend codes and world invites still work" in busy.error
    blocker.close()


# ---- log parsing / service ----
def test_log_detection(tmp_path):
    logs = tmp_path / "logs"; logs.mkdir()
    (logs / "latest.log").write_text("[Render thread/INFO]: Started serving on 51234\n"
                                     "[e4mc] Local game hosted on domain [cool-fox.eu.e4mc.link]\n", encoding="utf-8")
    assert find_shared_address(tmp_path) == "cool-fox.eu.e4mc.link"
    assert find_lan_port(tmp_path) == 51234
    with (logs / "latest.log").open("a") as fh:
        fh.write("[Server thread/INFO]: Stopping singleplayer server as player logged out\n")
    assert find_shared_address(tmp_path) is None and find_lan_port(tmp_path) is None


def test_service_invite_flow(tmp_path):
    node = lan.LanPresence(lambda: {"id": "X"}, port=0, targets=[])
    svc = FriendsService(tmp_path, lan=node, name=lambda: "itz_alone10", clock=lambda: 5000)
    with pytest.raises(CodeError, match="Open to LAN"):
        svc.invite_for(tmp_path, "1.21.11")
    out = svc.invite_for(tmp_path, "1.21.11", address="cool-fox.eu.e4mc.link")
    inv = svc.read_invite(out["invite"])
    assert inv["hostName"] == "itz_alone10" and inv["version"] == "1.21.11" and out["link"].startswith("vanta://join/VW1.")
    ov = svc.overview()
    assert ov["friends"] == [] and ov["lan"]["ok"] and ov["me"]["link"].startswith("vanta://friend/itz_alone10-")
    with pytest.raises(CodeError):
        svc.remove("NOPE")
