import time
from types import SimpleNamespace

from vanta.launcher.minecraft import playtime


def test_reset_one_instance_only(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    now = time.time()
    playtime.record(tmp_path, a, now - 600, now)
    playtime.record(tmp_path, b, now - 300, now)
    assert playtime.reset(tmp_path, a) == 600
    assert playtime.for_instance(tmp_path, a)["seconds"] == 0
    assert playtime.total(tmp_path) == 300
    assert playtime.reset(tmp_path, a) == 0


def test_app_reset_and_route(tmp_path):
    from vanta.launcher.ui.app import VantaApp
    app = VantaApp.__new__(VantaApp)
    app.data_dir = tmp_path
    app.log = SimpleNamespace(info=lambda *a, **k: None)
    inst = tmp_path / "1.21.1-fabric"
    app._instance_dir = lambda body: (inst, {"versionNumber": "1.21.1"})
    playtime.record(tmp_path, inst, time.time() - 120, time.time())
    assert app.reset_instance_playtime({})["cleared"] == 120
    s = open("vanta/launcher/ui/server.py", encoding="utf-8").read()
    assert "/api/instances/playtime/reset" in s
    assert 'id="inst-pt-reset"' in open("vanta/launcher/ui/index.html", encoding="utf-8").read()
