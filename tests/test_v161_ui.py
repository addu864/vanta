from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "vanta/launcher/ui/index.html").read_text(encoding="utf-8")


def test_log_search_and_filters():
    for needle in ('id="log-filter"', 'id="log-errors"', 'id="log-count"', "function renderLog", "logShown"):
        assert needle in HTML


def test_whats_new_popup_once_after_update(tmp_path, monkeypatch):
    import vanta
    from vanta.launcher.ui.app import VantaApp
    from vanta.launcher.updates import whatsnew
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    app = VantaApp()
    assert app.whats_new_popup()["show"] is False  # first run: just remember
    (tmp_path / "seen-version.json").write_text('{"version": "1.5.0"}')
    monkeypatch.setattr(whatsnew, "whats_new", lambda *a, **k: {"releases": [
        {"tag": "v" + vanta.__version__, "current": True, "name": "Vanta X", "highlights": ["A", "B"]}]})
    r = app.whats_new_popup()
    assert r["show"] and r["highlights"] == ["A", "B"] and r["seen"] == "1.5.0"
    app.whats_new_popup({"mark": True})
    assert app.whats_new_popup()["show"] is False
    assert 'id="whatsnew"' in HTML and "/api/whatsnew" in HTML


def test_whats_new_after_160_without_seen_file(tmp_path, monkeypatch):
    import vanta
    from vanta.launcher.ui.app import VantaApp
    from vanta.launcher.updates import whatsnew
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    (tmp_path / "appearance.json").write_text('{"tour": "done"}')
    monkeypatch.setattr(whatsnew, "whats_new", lambda *a, **k: {"releases": []})
    r = VantaApp().whats_new_popup()
    assert r["show"] == (vanta.__version__ != "1.6.0") and r.get("seen") == "1.6.0"
