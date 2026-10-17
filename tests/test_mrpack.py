import hashlib
import io
import json
import zipfile

import pytest

from vanta.launcher.minecraft import mrpack
from vanta.launcher.minecraft.mrpack import PackError

JAR = b"PK fake sodium jar"
URL = "https://cdn.modrinth.com/data/AANobbMI/versions/x/sodium.jar"


def entry(path="mods/sodium.jar", data=JAR, url=URL, env=None):
    e = {"path": path, "hashes": {"sha1": hashlib.sha1(data).hexdigest(), "sha512": hashlib.sha512(data).hexdigest()},
         "downloads": [url], "fileSize": len(data)}
    if env:
        e["env"] = env
    return e


def make_pack(files, deps=None, overrides=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("modrinth.index.json", json.dumps({"formatVersion": 1, "game": "minecraft", "versionId": "1.0", "name": "Test Pack",
                                                       "files": files, "dependencies": deps or {"minecraft": "1.21.1", "fabric-loader": "0.16.5"}}))
        for name, data in (overrides or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def index_of(pack):
    return mrpack.read_index(zipfile.ZipFile(io.BytesIO(pack)))


def test_read_index_ok_and_skips_server_only():
    info = index_of(make_pack([entry(), entry("mods/server.jar", b"s", env={"client": "unsupported", "server": "required"})]))
    assert info["minecraft"] == "1.21.1" and info["loader"] == "fabric" and [f["path"] for f in info["files"]] == ["mods/sodium.jar"]


@pytest.mark.parametrize("pack,match", [
    (make_pack([], deps={"minecraft": "1.20.1", "forge": "47.2.0"}), "Fabric packs only"),
    (make_pack([entry(url="https://evil.example.com/x.jar")]), "won't download"),
    (make_pack([entry(url="http://cdn.modrinth.com/x.jar")]), "won't download"),
    (make_pack([entry(path="../../evil.jar")]), "Unsafe"),
    (make_pack([entry(path="/abs/evil.jar")]), "Unsafe"),
    (make_pack([entry(path="C:/evil.jar")]), "Unsafe"),
    (make_pack([], deps={"fabric-loader": "1"}), "Minecraft version"),
])
def test_read_index_rejects(pack, match):
    with pytest.raises(PackError, match=match):
        index_of(pack)


def test_not_a_pack():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.txt", "hi")
    with pytest.raises(PackError, match="modrinth.index.json"):
        index_of(buf.getvalue())


def test_apply_verifies_backs_up_and_extracts(tmp_path):
    inst = tmp_path / "1.21.1-fabric"
    (inst / "mods").mkdir(parents=True)
    (inst / "mods" / "old.jar").write_bytes(b"old")
    (inst / "options.txt").write_text("toggleUse:false\n")
    (inst / "saves" / "World").mkdir(parents=True)
    pack = make_pack([entry()], overrides={"overrides/config/sodium.json": "{}", "client-overrides/options.txt": "fov:90\n",
                                           "overrides/../escape.txt": "x"})
    with pytest.raises(PackError, match="Unsafe"):
        mrpack.apply_pack(inst, pack, fetch=lambda u: JAR)
    pack = make_pack([entry()], overrides={"overrides/config/sodium.json": "{}", "client-overrides/options.txt": "fov:90\n"})
    seen = []
    out = mrpack.apply_pack(inst, pack, fetch=lambda u: seen.append(u) or JAR)
    assert seen == [URL] and out["files"] == 1 and out["overrides"] == 2
    assert (inst / "mods" / "sodium.jar").read_bytes() == JAR and not (inst / "mods" / "old.jar").exists()
    assert (inst / out["backup"] / "mods" / "old.jar").read_bytes() == b"old"
    assert (inst / "options.txt").read_text() == "fov:90\n" and (inst / "config" / "sodium.json").is_file()
    assert (inst / "saves" / "World").is_dir()  # worlds are never touched
    assert json.loads((inst / "vanta-modpack.json").read_text())["name"] == "Test Pack"


def test_apply_hash_mismatch_changes_nothing(tmp_path):
    inst = tmp_path / "i"
    (inst / "mods").mkdir(parents=True)
    (inst / "mods" / "keep.jar").write_bytes(b"k")
    with pytest.raises(PackError, match="hash"):
        mrpack.apply_pack(inst, make_pack([entry()]), fetch=lambda u: b"tampered")
    assert (inst / "mods" / "keep.jar").is_file() and not list(inst.glob("vanta-backup-*"))


def test_export_links_modrinth_files_and_bundles_the_rest(tmp_path):
    inst = tmp_path / "1.20.1-fabric"
    (inst / "mods").mkdir(parents=True)
    (inst / "mods" / "sodium.jar").write_bytes(JAR)
    (inst / "mods" / "mine.jar").write_bytes(b"homemade")
    (inst / "mods" / "off.jar.disabled").write_bytes(b"off")
    (inst / "config").mkdir()
    (inst / "config" / "a.json").write_text("{}")
    (inst / "options.txt").write_text("fov:80\n")
    sha1 = hashlib.sha1(JAR).hexdigest()
    lookup = lambda hashes: {sha1: {"files": [{"url": URL, "hashes": {"sha1": sha1}}]}}
    out = mrpack.export_pack(inst, tmp_path / "out" / "p.mrpack", name="Mine", minecraft="1.20.1", fabric_loader="0.16.5", lookup=lookup)
    assert out["linked"] == 1 and out["bundled"] == 1
    z = zipfile.ZipFile(tmp_path / "out" / "p.mrpack")
    idx = json.loads(z.read("modrinth.index.json"))
    assert idx["dependencies"] == {"minecraft": "1.20.1", "fabric-loader": "0.16.5"} and idx["files"][0]["downloads"] == [URL]
    assert {"overrides/mods/mine.jar", "overrides/config/a.json", "overrides/options.txt"} <= set(z.namelist())
    assert not any("off.jar" in n for n in z.namelist())
    # round trip: the exported pack imports cleanly
    target = tmp_path / "again"
    res = mrpack.apply_pack(target, (tmp_path / "out" / "p.mrpack").read_bytes(), fetch=lambda u: JAR)
    assert (target / "mods" / "sodium.jar").read_bytes() == JAR and (target / "mods" / "mine.jar").read_bytes() == b"homemade"
    assert res["backup"] == ""


def test_export_offline_bundles_everything(tmp_path):
    inst = tmp_path / "i"
    (inst / "mods").mkdir(parents=True)
    (inst / "mods" / "a.jar").write_bytes(b"a")
    def offline(_):
        raise OSError("no network")
    out = mrpack.export_pack(inst, tmp_path / "p.mrpack", name="x", minecraft="1.21.1", lookup=offline)
    assert out["linked"] == 0 and out["bundled"] == 1
