import json

from vanta.launcher.updates import whatsnew

RELS = [
    {"tag_name": "v1.5.0", "name": "Vanta 1.5.0", "published_at": "2026-10-16T10:00:00Z", "body": "## New\n- **Friends** tab\n- LAN [presence](http://x)\nnot a bullet", "html_url": "u1"},
    {"tag_name": "v1.5.1-mac", "name": "mac", "body": "- mac only"},
    {"tag_name": "v1.6.0", "name": "Vanta 1.6.0", "published_at": "2026-10-18T01:00:00Z", "body": "* Modpacks\n* Worlds"},
    {"tag_name": "v1.7.0", "draft": True, "body": "- secret"},
]


def test_highlights_strip_markdown():
    assert whatsnew.highlights(RELS[0]["body"]) == ["Friends tab", "LAN presence"]


def test_summarize_skips_mac_and_drafts_and_flags_current():
    rows = whatsnew.summarize(RELS, current="1.5.0")
    assert [r["tag"] for r in rows] == ["v1.6.0", "v1.5.0"]
    assert rows[0]["newer"] and rows[1]["current"]
    assert rows[0]["date"] == "2026-10-18"


def test_cache_and_offline(tmp_path):
    cache = tmp_path / "c" / "releases.json"
    calls = []

    def fetch(url):
        calls.append(url)
        return RELS

    r = whatsnew.whats_new(cache, fetch, current="1.6.0", now=1000.0)
    assert r["releases"][0]["current"] and len(calls) == 1
    whatsnew.whats_new(cache, fetch, current="1.6.0", now=2000.0)
    assert len(calls) == 1  # cached

    def boom(url):
        raise OSError("offline")

    r = whatsnew.whats_new(cache, boom, current="1.6.0", now=1000.0 + whatsnew.CACHE_SECONDS + 5)
    assert r["stale"] and r["releases"]
    r = whatsnew.whats_new(tmp_path / "none.json", boom, current="1.6.0")
    assert r["offline"] and r["releases"] == []
    assert json.loads(cache.read_text())["releases"][0]["tag_name"] == "v1.5.0"


def test_long_bullets_shortened():
    body = "- Friend code. Each install gets a short code. Plus a link that is very long " + "x" * 200
    assert whatsnew.highlights(body) == ["Friend code. Each install gets a short code."]
    assert len(whatsnew.highlights("- " + "y" * 300)[0]) == 120
