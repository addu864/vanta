from types import SimpleNamespace


def test_notes_fields(tmp_path):
    from vanta.launcher.ui.app import VantaApp
    app = VantaApp.__new__(VantaApp)
    app._instance_settings = lambda folder: {"notes": "seed  -42\nserver: play.example.net " + "x" * 400}
    f = app._notes_fields(tmp_path)
    assert f["notes"] is True and f["notesText"].startswith("seed -42 server: play.example.net") and len(f["notesText"]) == 300
    app._instance_settings = lambda folder: {"notes": "   "}
    assert app._notes_fields(tmp_path) == {"notes": False, "notesText": ""}


def test_palette_searches_instances():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert "window.palInstances = inst;" in s and "PALETTE.concat(insts)" in s
