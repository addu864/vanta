"""Required mods install only when Vanta creates an instance."""

from __future__ import annotations

from pathlib import Path

from vanta.launcher.minecraft.launch import _sync_profile_mods
from vanta.launcher.mods.models import ModDependency, ModFile, ModProject, ModVersion
from vanta.launcher.mods.repository import ModRepository
from vanta.launcher.mods.required import install_required_mods
from vanta.launcher.settings.profiles import ProfileStore


class FakeMods(ModRepository):
    name = "fake"

    def __init__(self) -> None:
        self.downloads = 0

    def search(self, query, *, game_version, loader, limit=20, offset=0):
        return []

    def get_project(self, project_id_or_slug):
        key = project_id_or_slug
        if key in {"fabric-api", "P7dR8mSH"}:
            return _project("P7dR8mSH", "fabric-api", "Fabric API")
        if key in {"modernfix", "mod-modern"}:
            return _project("mod-modern", "modernfix", "ModernFix")
        if key in {"sodium", "mod-sodium"}:
            return _project("mod-sodium", "sodium", "Sodium")
        if key in {"starlight", "mod-star"}:
            return _project("mod-star", "starlight", "Starlight")
        raise AssertionError(key)

    def get_versions(self, project_id_or_slug, *, game_version=None, loader=None):
        if project_id_or_slug in {"mod-modern", "modernfix"}:
            return []
        if project_id_or_slug in {"P7dR8mSH", "fabric-api"}:
            return [_version("fab-1", "P7dR8mSH", "fabric-api.jar", "https://cdn.modrinth.com/data/fabric-api.jar")]
        if project_id_or_slug in {"mod-sodium", "sodium"}:
            version = _version("sod-1", "mod-sodium", "sodium.jar", "https://cdn.modrinth.com/data/sodium.jar")
            version.dependencies = [
                ModDependency("P7dR8mSH", None, "required"),
                ModDependency("mod-star", None, "required"),
            ]
            return [version]
        return []

    def get_version(self, version_id):
        raise AssertionError(version_id)

    def download_file(self, url):
        self.downloads += 1
        return b"jar-bytes-" + url.encode("utf-8")


def _project(project_id, slug, title):
    return ModProject(
        id=project_id,
        slug=slug,
        title=title,
        description="",
        author="",
        icon_url=None,
        project_type="mod",
        game_versions=["1.21.11"],
        loaders=["fabric"],
        source="fake",
    )


def _version(version_id, project_id, filename, url):
    return ModVersion(
        id=version_id,
        project_id=project_id,
        name=version_id,
        version_number="1",
        game_versions=["1.21.11"],
        loaders=["fabric"],
        files=[ModFile(filename=filename, url=url, size=0, primary=True, hashes={})],
        source="fake",
    )


def test_required_install_skips_missing_build_and_starlight(tmp_path: Path, monkeypatch) -> None:
    import vanta.launcher.mods.required as required

    monkeypatch.setattr(required, "REQUIRED_MODS", (
        required.RequiredMod("sodium", "sodium", "Sodium", "performance"),
        required.RequiredMod("modernfix", "modernfix", "ModernFix", "performance"),
    ))
    repo = FakeMods()
    present = tmp_path / "mods"
    present.mkdir()
    (present / "already.jar").write_bytes(b"leave-me")
    result = install_required_mods(present, repository=repo)
    names = sorted(path.name for path in present.glob("*.jar"))
    assert "already.jar" in names
    assert "sodium.jar" in names
    assert "fabric-api.jar" in names
    assert "starlight" not in " ".join(names)
    skipped = {item["slug"]: item["reason"] for item in result["skipped"]}
    assert "modernfix" in skipped
    assert "1.21.11" in skipped["modernfix"]
    assert result["minecraftLaunched"] is False


def test_linked_instance_folder_is_not_modified(tmp_path: Path) -> None:
    instance = tmp_path / "linked"
    instance.mkdir()
    (instance / "client.jar").write_bytes(b"client")
    mods = instance / "mods"
    mods.mkdir()
    (mods / "custom.jar").write_bytes(b"custom")
    store = ProfileStore(tmp_path / "data", default_ram=4)
    store.create_from_instance("Linked", 4, str(instance))
    assert (mods / "custom.jar").read_bytes() == b"custom"
    assert not (mods / "required-mods.json").exists()


def test_profile_sync_keeps_required_jars(tmp_path: Path) -> None:
    instance = tmp_path / "instance"
    mods = instance / "mods"
    mods.mkdir(parents=True)
    (mods / "sodium.jar").write_bytes(b"required")
    (mods / "old-synced.jar").write_bytes(b"old")
    (mods / "required-mods.json").write_text('{"filenames": ["sodium.jar"]}', encoding="utf-8")
    (mods / ".vanta-synced").write_text("sodium.jar\nold-synced.jar\n", encoding="utf-8")
    _sync_profile_mods("Empty", instance, tmp_path / "data")
    assert (mods / "sodium.jar").read_bytes() == b"required"
    assert not (mods / "old-synced.jar").exists()
