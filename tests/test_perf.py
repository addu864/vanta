from vanta.shared import perf


def test_recommend_scales_with_mods_and_caps_by_memory():
    assert perf.recommend(10, 0, 0, 16)["ramGb"] == 3
    assert perf.recommend(40, 0, 0, 16)["ramGb"] == 4
    assert perf.recommend(5, 1, 0, 16)["ramGb"] == 4
    assert perf.recommend(150, 0, 0, 16)["ramGb"] == 6
    assert perf.recommend(300, 0, 0, 32)["ramGb"] == 8
    r = perf.recommend(300, 0, 0, 8)
    assert r["ramGb"] == 4 and "capped" in r["reason"]
    assert perf.recommend(300, 0, 0, 4)["ramGb"] == 2
    assert perf.recommend(300, 0, 0, 8, intel_mac=True)["ramGb"] == 3
    assert perf.recommend(10, 0, 5, 16)["ramGb"] == 4
    assert perf.recommend(10)["ramGb"] == 3  # unknown memory: no cap


def test_count_and_system_memory(tmp_path):
    (tmp_path / "mods").mkdir()
    (tmp_path / "mods" / "a.jar").write_bytes(b"")
    (tmp_path / "mods" / "b.jar.disabled").write_bytes(b"")
    (tmp_path / "shaderpacks").mkdir()
    (tmp_path / "shaderpacks" / "s.zip").write_bytes(b"")
    assert perf.count_content(tmp_path) == {"mods": 1, "shaders": 1, "resourcepacks": 0}
    assert perf.system_memory_gb() >= 0
