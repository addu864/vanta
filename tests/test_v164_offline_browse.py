import json
import os
import time
import urllib.error

from vanta.launcher.mods.browser import ModBrowser


class Net:
    def __init__(self):
        self.up = True
        self.calls = 0

    def __call__(self, req, timeout=0):
        self.calls += 1
        if not self.up:
            raise urllib.error.URLError("no route")
        hits = [{"project_id": "AANobbMI", "slug": "sodium", "title": "Sodium", "author": "jellysquid", "description": "Fast rendering",
                 "project_type": "mod", "downloads": 9, "versions": ["1.21.1"], "categories": ["optimization"]},
                {"project_id": "P7dR8mSH", "slug": "fabric-api", "title": "Fabric API", "author": "modmuss", "description": "Core hooks",
                 "project_type": "mod", "downloads": 99, "versions": ["1.21.1"], "categories": ["library"]}]

        class R:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return json.dumps({"hits": hits}).encode()
        return R()


def test_stale_cache_served_and_marked(tmp_path):
    net = Net()
    b = ModBrowser(tmp_path, opener=net)
    assert b.search("sodium", game_version="1.21.1")
    for f in tmp_path.glob("*.json"):
        os.utime(f, (time.time() - 99999, time.time() - 99999))
    b._mem.clear()
    net.up = False
    b.stale_reset()
    res = b.search("sodium", game_version="1.21.1")
    assert res and b.stale_since() is not None and b.offline


def test_backoff_skips_network(tmp_path):
    net = Net()
    net.up = False
    b = ModBrowser(tmp_path, opener=net)
    for q in ("a", "b", "c"):
        try:
            b.search(q, game_version="1.21.1")
        except Exception:
            pass
    assert net.calls == 1  # later calls answer straight away instead of timing out


def test_offline_search_over_saved_answers(tmp_path):
    net = Net()
    b = ModBrowser(tmp_path, opener=net)
    b.search("", game_version="1.21.1")
    items, saved = b.offline_search("fast", game_version="1.21.1")
    assert [i["title"] for i in items] == ["Sodium"] and saved
    items, _ = b.offline_search("", game_version="1.21.1")
    assert items[0]["title"] == "Fabric API"  # most downloaded first
    assert b.offline_search("sodium", game_version="1.8.9")[0] == []


def test_ui_has_offline_note():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert 'id="mb-offline"' in s and "function mbOffline" in s and "saved results" in s
