import time

from vanta.launcher.minecraft import playtime


def test_week_total_counts_only_recent_sessions(tmp_path):
    inst = tmp_path / "a"
    other = tmp_path / "b"
    now = time.time()
    playtime.record(tmp_path, inst, now - 10 * 86400 - 3600, now - 10 * 86400)  # 10 days ago
    playtime.record(tmp_path, inst, now - 2 * 86400 - 1800, now - 2 * 86400)
    playtime.record(tmp_path, other, now - 900, now)
    assert playtime.recent_total(tmp_path) == 1800 + 900
    assert playtime.recent_total(tmp_path, instance=inst) == 1800
    row = playtime.for_instance(tmp_path, inst)
    assert row["week"] == 1800 and row["seconds"] == 3600 + 1800


def test_recent_list_is_capped(tmp_path):
    inst = tmp_path / "a"
    now = time.time()
    for i in range(80):
        playtime.record(tmp_path, inst, now - 100000 + i * 1000, now - 100000 + i * 1000 + 60)
    data = playtime._load(tmp_path)["instances"][playtime._key(inst)]
    assert len(data["recent"]) == 60 and data["sessions"] == 80


def test_crash_card_has_check_mods():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert 'id="crash-check"' in s and "keep keyboard focus inside the dialog" in s
