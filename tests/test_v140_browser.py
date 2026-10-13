"""v1.4.0: Modrinth browser (rows, cache, UA, installs by project type, deps, remove)."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import urllib.parse
from pathlib import Path

import pytest

from vanta.launcher.mods import browser as B


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_api():
    files = {"https://cdn.modrinth.com/a.jar": b"A-JAR", "https://cdn.modrinth.com/lib.jar": b"LIB",
             "https://cdn.modrinth.com/sh.zip": b"SHADER", "https://cdn.modrinth.com/rp.zip": b"PACK"}
    projects = {
        "aaa": {"id": "aaa", "slug": "alpha", "title": "Alpha", "project_type": "mod", "team": "t1",
                "game_versions": ["1.21.11"], "loaders": ["fabric"], "downloads": 10, "body": "# Hi <script>x</script>"},
        "lib": {"id": "lib", "slug": "libby", "title": "Libby", "project_type": "mod", "team": "t1",
                "game_versions": ["1.21.11"], "loaders": ["fabric"]},
        "shd": {"id": "shd", "slug": "shady", "title": "Shady", "project_type": "shader", "game_versions": ["1.21.11"]},
        "rpk": {"id": "rpk", "slug": "packy", "title": "Packy", "project_type": "resourcepack", "game_versions": ["1.21.11"]},
    }
    for p in list(projects.values()):
        projects[p["slug"]] = p

    def ver(pid, url, name, deps=(), loaders=("fabric",)):
        return {"id": "v" + pid, "project_id": pid, "version_number": "1", "version_type": "release",
                "game_versions": ["1.21.11"], "loaders": list(loaders), "dependencies": list(deps),
                "files": [{"url": url, "filename": name, "primary": True,
                           "hashes": {"sha1": hashlib.sha1(files[url]).hexdigest()}}]}
    versions = {"aaa": [ver("aaa", "https://cdn.modrinth.com/a.jar", "alpha.jar",
                            [{"project_id": "lib", "dependency_type": "required"}])],
                "lib": [ver("lib", "https://cdn.modrinth.com/lib.jar", "libby.jar")],
                "shd": [ver("shd", "https://cdn.modrinth.com/sh.zip", "Shady.zip", loaders=("iris",))],
                "rpk": [ver("rpk", "https://cdn.modrinth.com/rp.zip", "Packy.zip", loaders=("minecraft",))]}
    calls = []

    def opener(req, timeout=0):
        calls.append(req)
        url = req.full_url
        if url in files:
            return Resp(files[url])
        u = urllib.parse.urlparse(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = u.path.replace("/v2", "", 1)
        if path == "/search":
            hits = [{"project_id": f"p{i}", "slug": f"s{i}", "title": f"T{i}", "downloads": 100000 + i, "author": "x"} for i in range(30)]
            return Resp(json.dumps({"hits": hits}).encode())
        if path == "/projects":
            ids = json.loads(q["ids"])
            return Resp(json.dumps([projects[i] for i in ids if i in projects]).encode())
        if path == "/teams":
            return Resp(json.dumps([[{"team_id": "t1", "is_owner": True, "user": {"username": "owner1"}}]]).encode())
        if path.startswith("/project/") and path.endswith("/version"):
            return Resp(json.dumps(versions[projects[path.split("/")[2]]["id"]]).encode())
        if path.startswith("/project/"):
            return Resp(json.dumps(projects[path.split("/")[2]]).encode())
        if path in ("/version_files", "/version_files/update"):
            return Resp(b"{}")
        raise AssertionError(url)
    return opener, calls


@pytest.fixture
def br(tmp_path):
    opener, calls = make_api()
    b = B.ModBrowser(tmp_path / "cache", opener=opener)
    b.calls = calls
    return b


def test_user_agent_and_cache(br):
    br.row("top", "1.21.11")
    br.row("top", "1.21.11")
    api_calls = [c for c in br.calls if "/search" in c.full_url]
    assert len(api_calls) == 1
    ua = api_calls[0].get_header("User-agent")
    assert ua.startswith("addu864/vanta/") and "github.com/addu864/vanta" in ua
    assert api_calls[0].full_url.startswith("https://api.modrinth.com/v2/")


def test_disk_cache_survives_restart(br, tmp_path):
    br.row("top", "1.21.11")
    opener, calls = make_api()
    again = B.ModBrowser(tmp_path / "cache", opener=opener)
    again.row("top", "1.21.11")
    assert not [c for c in calls if "/search" in c.full_url]


def test_specials_rotate_by_date(br):
    a = [c["id"] for c in br.specials("1.21.11", "fabric", dt.date(2026, 10, 13))]
    b = [c["id"] for c in br.specials("1.21.11", "fabric", dt.date(2026, 10, 13))]
    c = [c["id"] for c in br.specials("1.21.11", "fabric", dt.date(2026, 10, 14))]
    assert a == b and a != c and len(a) == 10


def test_all_rows_present(br):
    keys = [r["key"] for r in br.rows("1.21.11")]
    assert keys == ["specials", "essentials", "qol", "performance", "shaders", "resourcepacks", "top", "new"]


def test_search_facets_follow_type(br):
    br.search("x", game_version="1.21.11", project_type="shader")
    url = br.calls[-1].full_url
    facets = json.loads(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["facets"][0])
    assert ["project_type:shader"] in facets and ["versions:1.21.11"] in facets
    assert not any(f[0].startswith("categories:fabric") for f in facets)


def test_install_resolves_required_deps(br, tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    res = br.install(inst, "alpha", "1.21.11")
    assert res["ok"]
    assert (inst / "mods" / "alpha.jar").read_bytes() == b"A-JAR"
    assert (inst / "mods" / "libby.jar").is_file()
    idx = br.read_index(inst)
    assert set(idx) == {"aaa", "lib"}


def test_shader_and_pack_folders(br, tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    br.install(inst, "shady", "1.21.11")
    br.install(inst, "packy", "1.21.11")
    assert (inst / "shaderpacks" / "Shady.zip").is_file()
    assert (inst / "resourcepacks" / "Packy.zip").is_file()
    assert not (inst / "mods").exists()


def test_remove(br, tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    br.install(inst, "shady", "1.21.11")
    assert br.remove(inst, "shd", "1.21.11")["ok"]
    assert not (inst / "shaderpacks" / "Shady.zip").exists()
    assert br.remove(inst, "shd", "1.21.11")["ok"] is False


def test_refuses_non_modrinth_download(br):
    with pytest.raises(B.BrowserError):
        br._download("https://evil.example/x.jar", {})


def test_details_author_and_safe_body(br):
    d = br.details("alpha", "1.21.11")
    assert d["author"] == "owner1"
    assert "<script>" not in d["body"]
    assert d["dependencies"][0]["title"] == "Libby" and d["dependencies"][0]["required"]


def test_ui_has_browser():
    html = (Path(__file__).resolve().parents[1] / "vanta/launcher/ui/index.html").read_text(encoding="utf-8")
    for needle in ('id="mb-rows"', 'id="mb-instance"', "/api/browse/rows", "/api/browse/install", "mbMarkdown"):
        assert needle in html
