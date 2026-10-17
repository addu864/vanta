import json
import threading
import time

from vanta.launcher.updates import whatsnew


def test_background_never_waits_and_refreshes(tmp_path):
    cache = tmp_path / "releases.json"
    gate = threading.Event()

    def slow_fetch(url):
        gate.wait(5)
        return [{"tag_name": "v1.6.3", "name": "Vanta 1.6.3", "body": "- Fresh note here", "published_at": "2026-10-18"}]

    t0 = time.time()
    out = whatsnew.whats_new(cache, fetch=slow_fetch, current="1.6.3", background=True)
    assert time.time() - t0 < 0.5
    assert out["refreshing"] and out["releases"][0]["tag"] == "v1.6.3"  # bundled notes meanwhile
    gate.set()
    for _ in range(50):
        if cache.is_file():
            break
        time.sleep(0.05)
    data = json.loads(cache.read_text())
    assert data["releases"][0]["tag_name"] == "v1.6.3"
    again = whatsnew.whats_new(cache, fetch=slow_fetch, current="1.6.3", background=True)
    assert "refreshing" not in again and again["releases"][0]["highlights"] == ["Fresh note here"]


def test_assets_get_etag_and_304(tmp_path, monkeypatch):
    import http.client
    from vanta.launcher.ui.app import VantaApp
    from vanta.launcher.ui.server import make_server
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    httpd = make_server(VantaApp(), "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        port = httpd.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", port)
        c.request("GET", "/assets/style.css"); r = c.getresponse(); r.read()
        tag = r.getheader("ETag")
        assert r.status == 200 and tag and r.getheader("Cache-Control") == "no-cache"
        c.request("GET", "/assets/style.css", headers={"If-None-Match": tag}); r = c.getresponse()
        assert r.status == 304 and r.read() == b""
        c.request("GET", "/api/status"); r = c.getresponse(); r.read()
        assert r.getheader("Cache-Control") == "no-store" and not r.getheader("ETag")
    finally:
        httpd.shutdown()
