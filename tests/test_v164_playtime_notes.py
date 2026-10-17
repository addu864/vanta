import json
import os
import time

from vanta.launcher.minecraft import monitor, playtime


def test_record_and_totals(tmp_path):
    inst = tmp_path / "game" / "1.21.1-fabric"
    inst.mkdir(parents=True)
    now = time.time()
    assert playtime.record(tmp_path, inst, now - 3600, now) == 3600
    assert playtime.record(tmp_path, inst, now - 3600, now) == 0  # same session twice
    assert playtime.record(tmp_path, inst, now - 600, now - 60) == 540
    assert playtime.record(tmp_path, inst, now - 99 * 3600, now) == playtime.MAX_SESSION
    row = playtime.for_instance(tmp_path, inst)
    assert row["sessions"] == 3 and row["seconds"] == 3600 + 540 + playtime.MAX_SESSION
    assert playtime.total(tmp_path) == row["seconds"]
    assert playtime.nice(3720) == "1h 2m" and playtime.nice(300) == "5m"


def test_session_end_uses_latest_log(tmp_path):
    inst = tmp_path / "i"
    (inst / "logs").mkdir(parents=True)
    log = inst / "logs" / "latest.log"
    log.write_text("x")
    start = time.time() - 4000
    os.utime(log, (start + 1800, start + 1800))
    assert abs(playtime.session_end(inst, start, start + 60) - (start + 1800)) < 1


def test_monitor_counts_a_finished_session(tmp_path, monkeypatch):
    inst = tmp_path / "game" / "1.20.1-fabric"
    inst.mkdir(parents=True)
    jar = inst / "client.jar"
    jar.write_bytes(b"x")
    started = time.time() - 1200
    (inst / "logs").mkdir()
    (inst / "logs" / "latest.log").write_text("[Render thread/INFO]: Stopping!")
    os.utime(inst / "logs" / "latest.log", (started + 900, started + 900))
    monkeypatch.setattr(monitor.launch, "_read_session", lambda d: {"pid": 4242, "jar": str(jar), "started": started})
    monkeypatch.setattr(monitor.launch, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(monitor.launch, "_cmdline_matches", lambda pid, j: False)
    monkeypatch.setattr(monitor.launch, "_clear_session", lambda d: None)
    monkeypatch.setattr(monitor.launch, "exit_code", lambda pid: 0)
    monitor._state.update(pid=0, since=0.0, jar="", crash=None, seen=0.0)
    monitor.game_status(tmp_path)
    row = playtime.for_instance(tmp_path, inst)
    assert row["sessions"] == 1 and abs(row["seconds"] - 900) <= 1


def test_notes_cleaned_and_saved(tmp_path, monkeypatch):
    from vanta.launcher.ui import app as appmod
    a = appmod.VantaApp.__new__(appmod.VantaApp)
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "vanta-settings.json").write_text(json.dumps({"ramGb": 6, "label": "Survival"}))
    monkeypatch.setattr(a, "_instance_dir", lambda body: (inst, {}), raising=False)
    res = a.save_instance_notes({"notes": "Seed 1234\r\nbase at 100 64 -20\x00\x07" + "z" * 3000})
    assert res["ok"] and res["notes"].startswith("Seed 1234\nbase at 100 64 -20z") and len(res["notes"]) == 2000
    data = json.loads((inst / "vanta-settings.json").read_text())
    assert data["ramGb"] == 6 and data["label"] == "Survival" and data["notes"] == res["notes"]
    assert a._instance_settings(inst)["notes"] == res["notes"]


def test_ui_has_notes_and_playtime():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert 'id="inst-notes"' in s and 'id="dash-playtime"' in s and "/api/instances/notes" in s and 'id="inst-check"' in s
