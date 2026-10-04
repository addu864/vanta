"""Vanta 0.3 skins tests. Network is mocked. A live Ely.by probe is documented separately."""

from __future__ import annotations

import base64
import json
import shutil
import struct
import subprocess
import threading
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import pytest

from vanta.launcher.accounts.store import LOCAL_LABEL
from vanta.launcher.settings.profiles import PERFORMANCE
from vanta.launcher.skins.elyby import ElyByRepository
from vanta.launcher.skins.models import LOCAL_APPLY_NOTE, SkinHit
from vanta.launcher.skins.repository import SkinRepository, SkinRepositoryError
from vanta.launcher.ui.app import VantaApp
from vanta.launcher.ui.pages import index_page
from vanta.launcher.ui.server import dispatch


def _png(width: int, height: int, rgba: tuple[int, int, int, int] = (220, 40, 40, 255)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    row = b"\x00" + bytes(rgba) * width
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b"")


class FakeSkinRepository(SkinRepository):
    name = "fake"

    def __init__(self) -> None:
        self.fail: str | None = None
        self.searches = 0
        self.fetches = 0
        self.hits = [
            SkinHit(id="ely:notch", name="Notch", username="Notch", source="fake", model="steve"),
            SkinHit(id="ely:alex", name="Alex", username="Alex", source="fake", model="alex"),
        ]
        self.textures = {
            "ely:notch": _png(64, 64),
            "ely:alex": _png(64, 64, (40, 80, 200, 255)),
        }

    def search(self, query: str, *, limit: int = 5) -> list[SkinHit]:
        self.searches += 1
        if self.fail:
            raise SkinRepositoryError(self.fail)
        needle = (query or "").strip().lower()
        if not needle:
            return []
        found = [
            hit
            for hit in self.hits
            if needle in hit.name.lower() or needle in (hit.username or "").lower() or needle in hit.id
        ]
        return found[:limit]

    def fetch_texture(self, skin_id: str) -> bytes:
        self.fetches += 1
        if self.fail:
            raise SkinRepositoryError(self.fail)
        png = self.textures.get(skin_id)
        if png is None:
            raise SkinRepositoryError(f"No texture for {skin_id}.")
        return png

    def describe(self) -> dict:
        return {"name": self.name, "secretRequired": False, "catalogSearch": False}


def _app(tmp_path: Path, repo: FakeSkinRepository | None = None) -> tuple[VantaApp, FakeSkinRepository]:
    skins = repo or FakeSkinRepository()
    return VantaApp(tmp_path, skin_repository=skins), skins


def test_search_returns_mocked_hits_and_empty_lookup_is_not_a_network_error(tmp_path: Path) -> None:
    app, repo = _app(tmp_path)
    found = app.search_skins({"query": "Notch"})
    assert found["ok"] is True
    assert found["source"] == "fake"
    assert [item["id"] for item in found["results"]] == ["ely:notch"]
    assert found["results"][0]["model"] == "steve"
    assert found["results"][0]["uploadedToMicrosoft"] is False
    assert repo.searches == 1

    missing = app.search_skins({"query": "Nobody"})
    assert missing["ok"] is True
    assert missing["results"] == []
    assert missing["message"]
    assert "Network" not in missing["message"]


def test_network_failure_is_not_an_empty_success(tmp_path: Path) -> None:
    app, repo = _app(tmp_path)
    repo.fail = "Network error while contacting Ely.by: timed out."
    failed = app.search_skins({"query": "Notch"})
    assert failed["ok"] is False
    assert "results" not in failed
    assert "Network error" in failed["error"]
    assert failed.get("results") != []
    assert "Network error" in app.last_error()


def test_filter_favorites_and_model_without_treating_favorites_as_a_remote_search(tmp_path: Path) -> None:
    app, repo = _app(tmp_path)
    both = app.search_skins({"query": "ely"})
    assert {item["id"] for item in both["results"]} == {"ely:notch", "ely:alex"}
    fav = app.favorite_skin({"skinId": "ely:notch", "favorite": True})
    assert fav["ok"] is True
    assert fav["skin"]["favorite"] is True

    repo.fail = "Network error while contacting Ely.by: down."
    only = app.search_skins({"query": "notch", "filter": "favorites"})
    assert only["ok"] is True
    assert only["source"] == "local"
    assert [item["id"] for item in only["results"]] == ["ely:notch"]
    none = app.search_skins({"query": "alex", "filter": "favorites"})
    assert none["ok"] is True
    assert none["results"] == []
    assert none["message"]

    repo.fail = None
    alex_only = app.search_skins({"query": "ely", "filter": "alex"})
    assert alex_only["ok"] is True
    assert [item["id"] for item in alex_only["results"]] == ["ely:alex"]

    removed = app.favorite_skin({"skinId": "ely:notch", "favorite": False})
    assert removed["ok"] is True
    again = VantaApp(tmp_path, skin_repository=repo)
    assert again.skins.favorites()["results"] == []


def test_recent_list_persists_in_vanta_data(tmp_path: Path) -> None:
    app, repo = _app(tmp_path)
    app.search_skins({"query": "ely"})
    assert app.view_skin({"skinId": "ely:notch"})["ok"] is True
    assert app.view_skin({"skinId": "ely:alex"})["ok"] is True
    recent = app.skins.recent()["results"]
    assert [item["id"] for item in recent] == ["ely:alex", "ely:notch"]
    assert recent[0]["viewedAt"]

    reloaded = VantaApp(tmp_path, skin_repository=FakeSkinRepository())
    assert [item["id"] for item in reloaded.skins.recent()["results"]] == ["ely:alex", "ely:notch"]
    library = json.loads((tmp_path / "skins" / "library.json").read_text(encoding="utf-8"))
    assert library["recent"][0]["id"] == "ely:alex"
    assert repo.fetches == 0


def test_model_selection_is_stored_on_the_skin_and_local_account(tmp_path: Path) -> None:
    app, _repo = _app(tmp_path)
    created = app.create_local_account({"username": "Advay"})
    assert created["account"]["label"] == LOCAL_LABEL
    app.search_skins({"query": "Notch"})
    chosen = app.set_skin_model({"skinId": "ely:notch", "model": "alex"})
    assert chosen["ok"] is True
    assert chosen["skin"]["model"] == "alex"

    reloaded = VantaApp(tmp_path, skin_repository=FakeSkinRepository())
    assert reloaded.skins.library.get_record("ely:notch")["model"] == "alex"

    saved = reloaded.apply_skin({"skinId": "ely:notch", "accountId": created["account"]["id"]})
    assert saved["ok"] is True
    assert saved["account"]["skinModel"] == "alex"
    assert saved["account"]["label"] == "LOCAL TEST ACCOUNT"
    assert saved["uploadedToMicrosoft"] is False

    flipped = reloaded.set_skin_model({"skinId": "ely:notch", "model": "steve", "accountId": created["account"]["id"]})
    assert flipped["ok"] is True
    assert flipped["account"]["skinModel"] == "steve"
    disk = json.loads((tmp_path / "accounts.json").read_text(encoding="utf-8"))
    assert disk["accounts"][0]["skinModel"] == "steve"
    assert disk["accounts"][0]["skinUploadedToMicrosoft"] is False


def test_import_accepts_64x64_and_64x32_and_rejects_other_sizes(tmp_path: Path) -> None:
    app, repo = _app(tmp_path)
    good = app.import_skin({"name": "Hero", "model": "alex", "pngBase64": base64.b64encode(_png(64, 64)).decode()})
    assert good["ok"] is True
    assert good["skin"]["width"] == 64
    assert good["skin"]["height"] == 64
    assert good["skin"]["model"] == "alex"
    assert good["skin"]["source"] == "local-png"
    assert (tmp_path / good["skin"]["file"]).is_file()

    wide = app.import_skin({"name": "Legacy", "model": "steve", "pngBase64": base64.b64encode(_png(64, 32)).decode()})
    assert wide["ok"] is True
    assert wide["skin"]["height"] == 32

    before = list((tmp_path / "skins" / "files").glob("*.png"))
    bad = app.import_skin({"name": "Nope", "pngBase64": base64.b64encode(_png(32, 32)).decode()})
    assert bad["ok"] is False
    assert "64x64 or 64x32" in bad["error"]
    assert "32x32" in bad["error"]
    assert list((tmp_path / "skins" / "files").glob("*.png")) == before

    not_png = app.import_skin({"name": "Gif", "pngBase64": base64.b64encode(b"GIF89a not a skin").decode()})
    assert not_png["ok"] is False
    assert "not a PNG" in not_png["error"]
    assert repo.fetches == 0
    assert repo.searches == 0


def test_apply_saves_local_file_and_does_not_claim_microsoft_upload(tmp_path: Path) -> None:
    app, repo = _app(tmp_path)
    created = app.create_local_account({"username": "Advay"})
    app.search_skins({"query": "ely"})
    result = app.apply_skin({"skinId": "ely:notch", "model": "steve"})
    assert result["ok"] is True
    assert result["uploadedToMicrosoft"] is False
    assert result["note"] == LOCAL_APPLY_NOTE
    assert "Microsoft account, which is not configured" in result["note"]
    assert "not uploaded to Microsoft" in result["note"]
    skin_path = tmp_path / result["skinFile"]
    assert skin_path.is_file()
    meta = json.loads(skin_path.with_name("skin.json").read_text(encoding="utf-8"))
    assert meta["uploadedToMicrosoft"] is False
    assert meta["accountLabel"] == "LOCAL TEST ACCOUNT"
    assert result["account"]["skinFile"] == result["skinFile"]
    assert repo.fetches == 1

    repo.fail = "Network error while contacting Ely.by: timed out."
    failed = app.apply_skin({"skinId": "ely:alex"})
    assert failed["ok"] is False
    assert "Network error" in failed["error"]
    assert "results" not in failed
    account_dir = tmp_path / "skins" / "local-accounts" / created["account"]["id"]
    # The failed skin must not replace the saved local file with an empty success.
    assert (account_dir / "skin.json").read_text(encoding="utf-8").count("ely:notch") == 1


def test_elyby_repository_parses_textures_and_reports_network_errors() -> None:
    png = _png(64, 64)
    seen: list[str] = []

    class Response:
        def __init__(self, status: int, body: bytes) -> None:
            self.status = status
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    def opener(request, timeout=0):  # noqa: ANN001
        url = request.full_url
        seen.append(url)
        if url.endswith("/textures/Missing?version=2"):
            return Response(204, b"")
        if url.endswith("/textures/Down?version=2"):
            raise urllib.error.URLError("timed out")
        if "/textures/" in url:
            body = json.dumps(
                {"SKIN": {"url": "http://textures.minecraft.net/texture/abc", "metadata": {"model": "slim"}}}
            ).encode()
            return Response(200, body)
        if url.endswith("/skins/slim.png"):
            return Response(200, png)
        raise AssertionError(url)

    repo = ElyByRepository(opener=opener)
    hits = repo.search("Slim")
    assert len(hits) == 1
    assert hits[0].id == "ely:slim"
    assert hits[0].model == "alex"
    assert repo.search("Missing") == []
    with pytest.raises(SkinRepositoryError) as exc:
        repo.search("Down")
    assert "Network error" in exc.value.message
    texture = repo.fetch_texture("ely:slim")
    assert texture.startswith(b"\x89PNG")
    assert not any("namemc" in url.lower() for url in seen)
    info = repo.describe()
    assert info["secretRequired"] is False
    assert info["catalogSearch"] is False
    assert "NameMC" in info["limitation"]

    with pytest.raises(SkinRepositoryError) as bad:
        repo.search("skin-title")
    assert "username" in bad.value.message.lower()


def test_local_account_play_dry_run_still_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, _repo = _app(tmp_path)
    created = app.create_local_account({"username": "Advay"})
    assert created["account"]["label"] == "LOCAL TEST ACCOUNT"
    imported = app.import_skin({"name": "Hero", "model": "steve", "pngBase64": base64.b64encode(_png(64, 32)).decode()})
    applied = app.apply_skin({"skinId": imported["skin"]["id"], "model": "steve"})
    assert applied["ok"] is True
    assert applied["uploadedToMicrosoft"] is False
    monkeypatch.setattr(shutil, "which", lambda name: None)
    calls: list[str] = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append("run"))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: calls.append("popen"))
    result = app.play(
        {
            "accountId": created["account"]["id"],
            "versionNumber": "1.21.11",
            "loader": "fabric",
            "ramGb": 4,
            "profileName": PERFORMANCE,
            "targetMode": "local-dry-run",
        }
    )
    assert result["status"] == "Ready"
    assert result["launched"] is False
    assert result["gameStarted"] is False
    assert result["dryRun"] is True
    assert calls == []
    assert app.status()["version"] == "1.0.0"
    assert app.status()["milestone"] == "1.0"
    assert app.status()["skinsRepository"]["name"] == "fake"


def test_skins_http_ui_replaces_placeholder(tmp_path: Path) -> None:
    app, _repo = _app(tmp_path)
    html = index_page()
    assert ">SKINS<" in html
    assert "Save on local account" in html
    assert "WebGL" in html
    assert html.count("Coming in a later milestone") == 0
    assert "Milestone 1.0" in html

    status, body, content_type = dispatch(app, "POST", "/api/skins/search", json.dumps({"query": "Alex"}).encode())
    payload = json.loads(body.decode("utf-8"))
    assert status == 200
    assert payload["results"][0]["model"] == "alex"
    assert "application/json" in content_type

    failed_repo = FakeSkinRepository()
    failed_repo.fail = "Network error while contacting Ely.by: refused."
    failed_app = VantaApp(tmp_path / "other", skin_repository=failed_repo)
    status, body, _content_type = dispatch(
        failed_app, "POST", "/api/skins/search", json.dumps({"query": "Notch"}).encode()
    )
    payload = json.loads(body.decode("utf-8"))
    assert status == 400
    assert payload["ok"] is False
    assert "results" not in payload
    assert "Network error" in payload["error"]

    httpd_started = False
    from vanta.launcher.ui.server import make_server

    httpd = make_server(app, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    httpd_started = True
    port = httpd.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status") as response:
            status_body = json.loads(response.read().decode("utf-8"))
        assert status_body["version"] == "1.0.0"
        assert status_body["milestone"] == "1.0"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as response:
            page = response.read().decode("utf-8")
        assert "panel-skins" in page
        assert page.count("Coming in a later milestone") == 0
    finally:
        if httpd_started:
            httpd.shutdown()
            httpd.server_close()


def test_default_skin_library_is_local_and_does_not_call_ely(tmp_path: Path, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("A skin lookup tried to use the network.")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    app = VantaApp(tmp_path)
    info = app.status()["skinsRepository"]
    assert info["name"] == "vanta-library"
    assert info["offline"] is True
    assert "Ely.by" in info["limitation"]
    assert "NameMC" in info["limitation"]
    found = app.search_skins({"query": "", "limit": 24})
    assert found["ok"] is True
    assert found["source"] == "vanta-library"
    assert len(found["results"]) >= 6
    assert {item["model"] for item in found["results"]} == {"steve", "alex"}
    assert all(item["uploadedToMicrosoft"] is False for item in found["results"])
    assert all(item["source"] == "vanta-library" for item in found["results"])
    narrowed = app.search_skins({"query": "tide"})
    assert [item["id"] for item in narrowed["results"]] == ["vanta-tide"]
    created = app.create_local_account({"username": "Advay"})
    assert created["ok"] is True
    saved = app.apply_skin({"skinId": "vanta-classic", "model": "steve"})
    assert saved["ok"] is True
    assert saved["uploadedToMicrosoft"] is False
    assert saved["note"] == LOCAL_APPLY_NOTE
    status, body, content_type = dispatch(app, "GET", "/api/skins/texture?id=vanta-moss", b"")
    assert status == 200
    assert content_type == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"
    state = app.skins_state()
    assert len(state["catalog"]) >= 6
    assert "skinsystem.ely.by" not in str(state["repository"]).lower()
