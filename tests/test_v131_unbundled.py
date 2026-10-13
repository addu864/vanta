"""v1.3.1: no third-party packs/game files bundled; first-run + Trailer progress."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vanta.launcher.minecraft import first_run, instances
from vanta.launcher.mods import visual_set
from vanta.launcher.mods.models import ModFile, ModVersion


def test_first_run_enabled_by_default_on_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VANTA_AUTO_INSTALL", raising=False)
    monkeypatch.setattr(first_run.sys, "platform", "win32")
    assert first_run.enabled()
    monkeypatch.setattr(first_run.sys, "platform", "linux")
    assert not first_run.enabled()
    monkeypatch.setenv("VANTA_AUTO_INSTALL", "1")
    assert first_run.enabled()
    monkeypatch.setattr(first_run.sys, "platform", "win32")
    monkeypatch.setenv("VANTA_AUTO_INSTALL", "0")
    assert not first_run.enabled()


def test_first_run_reports_progress_and_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VANTA_AUTO_INSTALL", "1")
    seen: list[dict] = []

    def fake(root: Path, *, download_assets: bool, progress) -> dict:
        progress("client", 0, 1)
        seen.append(json.loads((tmp_path / first_run.STATUS_NAME).read_text()))
        progress("packs", 4, 5)
        jar = root / "1.21.11-fabric" / "client.jar"
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"x")
        return {"instance": str(jar.parent), "visualSet": {"skipped": [{"title": "Bare Bones", "reason": "offline"}]}}

    assert first_run.status(tmp_path)["needed"] is True
    assert first_run.maybe_start(tmp_path, installer=fake, background=False)
    assert seen[0]["state"] == "downloading" and seen[0]["step"] == "client"
    done = first_run.status(tmp_path)
    assert done["state"] == "done" and done["percent"] == 100 and done["needed"] is False
    assert done["skipped"] == ["Bare Bones: offline"]
    assert not first_run.maybe_start(tmp_path, installer=fake, background=False)  # already installed


def test_first_run_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VANTA_AUTO_INSTALL", "1")

    def boom(root: Path, **_: object) -> dict:
        raise OSError("no internet")

    first_run.maybe_start(tmp_path, installer=boom, background=False)
    st = first_run.status(tmp_path)
    assert st["state"] == "failed" and "no internet" in st["error"] and st["needed"]


class _Repo:
    def get_versions(self, project: str, game_version: str):
        name = f"{project}.zip"
        import hashlib
        return [ModVersion(id=project, project_id=project, name=project, version_number="1", game_versions=[game_version],
                           loaders=["minecraft", "iris"], files=[ModFile(url=f"https://cdn.modrinth.com/{name}", filename=name, primary=True,
                           hashes={"sha1": hashlib.sha1(name.encode()).hexdigest()})])]

    def download_file(self, url: str) -> bytes:
        return url.rsplit("/", 1)[1].encode()


def test_visual_set_downloads_with_progress(tmp_path: Path) -> None:
    steps: list[tuple[str, int, int]] = []
    res = visual_set.install_visual_set(tmp_path, repository=_Repo(), game_version="1.21.11", progress=lambda *a: steps.append(a))
    assert len(res["placed"]) == 5 and not res["skipped"]
    assert steps[0] == ("packs", 0, 5) and steps[-1] == ("packs", 5, 5)
    assert (tmp_path / "shaderpacks" / "HVnmMxH1.zip").is_file()


def test_trailer_progress_tracker() -> None:
    report = instances.track_trailer("1.20.1-fabric")
    assert instances.trailer_progress()["state"] == "running"
    report("packs", 2, 4)
    p = instances.trailer_progress()
    assert p["step"] == "packs" and p["percent"] == 70
    instances.finish_trailer(True)
    assert instances.trailer_progress()["state"] == "done"


def test_repo_ships_no_third_party_packs() -> None:
    root = Path(__file__).resolve().parents[1]
    names = [p.name.lower() for p in (root / "assets").rglob("*")]
    for bad in ("bare bones", "bare-bones", "freshanimations", "fresh-animations", "complementary", "client.jar"):
        assert not any(bad in n for n in names), bad


def test_interrupted_first_run_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jar = tmp_path / "game" / "1.21.11-fabric" / "client.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"x")
    assert first_run.needs_install(tmp_path) is False  # old install, no status file
    (tmp_path / first_run.STATUS_NAME).write_text(json.dumps({"state": "downloading"}))
    assert first_run.needs_install(tmp_path) is True
    assert first_run.status(tmp_path)["state"] == "interrupted"
