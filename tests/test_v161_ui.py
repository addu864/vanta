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


def test_screenshot_viewer_and_reveal_validation(tmp_path, monkeypatch):
    from vanta.launcher.ui.app import VantaApp
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    app = VantaApp()
    monkeypatch.setattr(app, "_instance_dir", lambda body: (tmp_path / "inst", {}))
    assert not app.reveal_screenshot({"name": "../evil.png"})["ok"]
    assert "gone" in app.reveal_screenshot({"name": "missing.png"})["error"]
    for needle in ('id="shot-viewer"', 'id="sv-copy"', "ClipboardItem", "/api/instances/screenshot-reveal"):
        assert needle in HTML


def test_server_favourites_and_last_seen(tmp_path, monkeypatch):
    from vanta.launcher.servers import popular
    from vanta.launcher.ui.app import VantaApp
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    app = VantaApp()
    servers = popular.load_servers(tmp_path)
    sid = servers[0]["id"]
    assert not app.server_favourite({"id": "nope", "on": True})["ok"]
    assert app.server_favourite({"id": sid, "on": True})["ids"] == [sid]
    monkeypatch.setattr(popular, "statuses", lambda ss: [dict(s, address=popular.address(s), status={"online": s["id"] == sid, "players": 7, "maxPlayers": 50, "latencyMs": 40}) for s in ss])
    rows = app.popular_servers()["servers"]
    first = next(r for r in rows if r["id"] == sid)
    assert first["favourite"] and first["status"]["latencyMs"] == 40
    quick = next(r for r in app.popular_servers({"live": "0"})["servers"] if r["id"] == sid)
    assert quick["last"]["players"] == 7
    assert app.server_favourite({"id": sid, "on": False})["ids"] == []
    assert 'id="browse-sort"' in HTML and "/api/popular-servers/favourite" in HTML


def test_banner_from_search_hits_and_projects():
    from vanta.launcher.mods.browser import card
    assert card({"featured_gallery": "https://cdn.modrinth.com/a.png"})["banner"] == "https://cdn.modrinth.com/a.png"
    assert card({"gallery": ["https://cdn.modrinth.com/b.png"]})["banner"] == "https://cdn.modrinth.com/b.png"
    assert card({"gallery": [{"url": "https://x/1.png"}, {"url": "https://x/2.png", "featured": True}]})["banner"] == "https://x/2.png"
    assert card({"gallery": [{"url": "http://insecure"}]})["banner"] is None
    assert "has-banner" in HTML
