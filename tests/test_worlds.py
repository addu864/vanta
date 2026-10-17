import gzip
import struct
import zipfile

import pytest

from vanta.launcher.minecraft import worlds
from vanta.launcher.minecraft.worlds import WorldError


def make_world(instance, folder, level="My World", extra=b"x" * 2000):
    w = instance / "saves" / folder
    (w / "region").mkdir(parents=True)
    name = level.encode()
    nbt = b"\x0a\x00\x00\x0a\x00\x04Data" + b"\x08\x00\x09LevelName" + struct.pack(">H", len(name)) + name + b"\x00\x00"
    (w / "level.dat").write_bytes(gzip.compress(nbt))
    (w / "region" / "r.0.0.mca").write_bytes(extra)
    (w / "session.lock").write_bytes(b"lock")
    return w


def test_list_reads_level_name_and_sorts(tmp_path):
    make_world(tmp_path, "New World", "Island")
    make_world(tmp_path, "b", "")
    (tmp_path / "saves" / "not-a-world").mkdir()
    rows = worlds.list_worlds(tmp_path)
    assert {r["folder"] for r in rows} == {"New World", "b"}
    assert next(r for r in rows if r["folder"] == "New World")["name"] == "Island"
    assert next(r for r in rows if r["folder"] == "b")["name"] == "b"
    assert worlds.list_worlds(tmp_path / "nope") == []


def test_backup_restore_roundtrip_never_overwrites(tmp_path):
    inst, bk = tmp_path / "inst", tmp_path / "bk"
    make_world(inst, "New World", "Island")
    out = worlds.backup(inst, "New World", bk)
    names = zipfile.ZipFile(bk / out["file"]).namelist()
    assert "New World/level.dat" in names and not any("session.lock" in n for n in names)
    assert worlds.list_backups(bk)[0]["world"] == "New World"
    r = worlds.restore(inst, bk, out["file"])
    assert r["renamed"] and r["folder"].startswith("New World (restored ")
    assert (inst / "saves" / r["folder"] / "region" / "r.0.0.mca").read_bytes() == b"x" * 2000
    assert (inst / "saves" / "New World" / "level.dat").is_file()
    # into an empty instance it keeps the original name
    other = tmp_path / "other"
    assert worlds.restore(other, bk, out["file"]) == {"ok": True, "folder": "New World", "renamed": False}


def test_delete_needs_typed_name_and_keeps_backup(tmp_path):
    inst, bk = tmp_path / "inst", tmp_path / "bk"
    make_world(inst, "Hardcore")
    with pytest.raises(WorldError, match="Type the world folder name"):
        worlds.delete(inst, "Hardcore", "hardcore", bk)
    assert (inst / "saves" / "Hardcore").is_dir()
    out = worlds.delete(inst, "Hardcore", "Hardcore", bk)
    assert not (inst / "saves" / "Hardcore").exists()
    assert "before-delete" in out["backup"] and worlds.list_backups(bk)[0]["auto"]


def test_bad_names_and_unsafe_zips(tmp_path):
    inst, bk = tmp_path / "inst", tmp_path / "bk"
    bk.mkdir()
    for name in ("../x", "..", "", "a/b", ".hidden"):
        with pytest.raises(WorldError):
            worlds.world_path(inst, name)
    for file in ("../evil.zip", "x.txt", ""):
        with pytest.raises(WorldError):
            worlds.restore(inst, bk, file)
    with zipfile.ZipFile(bk / "evil.zip", "w") as z:
        z.writestr("w/level.dat", b"x")
        z.writestr("w/../../escape.txt", b"x")
    with pytest.raises(WorldError):
        worlds.restore(inst, bk, "evil.zip")
    assert not (tmp_path / "escape.txt").exists()
    with zipfile.ZipFile(bk / "two.zip", "w") as z:
        z.writestr("a/level.dat", b"x")
        z.writestr("b/level.dat", b"x")
    with pytest.raises(WorldError, match="exactly one"):
        worlds.restore(inst, bk, "two.zip")
    with zipfile.ZipFile(bk / "nolevel.zip", "w") as z:
        z.writestr("a/data.txt", b"x")
    with pytest.raises(WorldError, match="level.dat"):
        worlds.restore(inst, bk, "nolevel.zip")
    worlds.delete_backup(bk, "two.zip")
    assert not (bk / "two.zip").exists()
