"""Trailer-look visual set: packs placed, options.txt order, Iris shader."""

from __future__ import annotations

import hashlib
from pathlib import Path

from vanta.launcher.mods.models import ModFile, ModVersion
from vanta.launcher.mods.repository import ModRepository
from vanta.launcher.mods import visual_set
from vanta.launcher.mods.required import REQUIRED_MODS


class FakeRepo(ModRepository):
    name = "fake"

    def __init__(self, missing: set[str] = frozenset()) -> None:
        self.missing = missing

    def search(self, *a, **k):
        return []

    def get_project(self, key):
        raise AssertionError(key)

    def get_version(self, key):
        raise AssertionError(key)

    def get_versions(self, project, *, game_version=None, loader=None):
        if project in self.missing:
            return []
        loaders = ["iris", "optifine"] if project in {"HVnmMxH1", "R6NEzAwj"} else ["minecraft"]
        body = f"zip-{project}".encode()
        return [ModVersion(
            id=f"v-{project}", project_id=project, name=project, version_number="1.0",
            game_versions=["1.21.11"], loaders=loaders,
            files=[ModFile(filename=f"{project}.zip", url=f"https://cdn.modrinth.com/data/{project}.zip",
                           size=len(body), primary=True, hashes={"sha1": hashlib.sha1(body).hexdigest()})],
            source="fake",
        )]

    def download_file(self, url):
        return f"zip-{url.rsplit('/', 1)[-1][:-4]}".encode()


def test_visual_set_places_packs_orders_options_and_sets_iris(tmp_path: Path) -> None:
    (tmp_path / "options.txt").write_text('version:4556\nresourcePacks:["vanilla","fabric"]\nincompatibleResourcePacks:[]\n')
    result = visual_set.install_visual_set(tmp_path, repository=FakeRepo())
    assert result["skipped"] == []
    assert (tmp_path / "resourcepacks" / "rox3U8B6.zip").is_file()
    assert (tmp_path / "shaderpacks" / "HVnmMxH1.zip").is_file()
    line = [l for l in (tmp_path / "options.txt").read_text().splitlines() if l.startswith("resourcePacks:")][0]
    assert line == 'resourcePacks:["vanilla","fabric","file/rox3U8B6.zip","file/50dA9Sha.zip","file/DCHWs5EF.zip"]'
    iris = (tmp_path / "config" / "iris.properties").read_text()
    assert "shaderPack=HVnmMxH1.zip" in iris
    assert "enableShaders=true" in iris
    # Running again does not duplicate entries.
    visual_set.install_visual_set(tmp_path, repository=FakeRepo())
    line2 = [l for l in (tmp_path / "options.txt").read_text().splitlines() if l.startswith("resourcePacks:")][0]
    assert line2 == line


def test_missing_build_is_reported_not_guessed(tmp_path: Path) -> None:
    result = visual_set.install_visual_set(tmp_path, repository=FakeRepo(missing={"50dA9Sha"}))
    assert [s["key"] for s in result["skipped"]] == ["fresh-animations"]
    assert not (tmp_path / "options.txt").exists()


def test_visual_mods_are_in_the_default_set() -> None:
    slugs = {m.slug for m in REQUIRED_MODS}
    assert {"iris", "sodium", "entity-model-features", "entitytexturefeatures", "not-enough-animations", "visuality"} <= slugs
