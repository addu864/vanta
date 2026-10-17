"""Intel / Apple Silicon Mac selection: arch probe, Java pick + download, LWJGL natives, JVM flags."""

import hashlib
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from vanta.launcher.minecraft import install, launch
from vanta.shared import java as javamod
from vanta.shared import java_download as jd
from vanta.shared import macplatform as mp


def fake_sysctl(values):
    def run(args, **_):
        key = args[-1]
        return SimpleNamespace(returncode=0 if key in values else 1, stdout=str(values.get(key, "")))
    return run


INTEL = {"hw.optional.arm64": "0", "sysctl.proc_translated": "0", "hw.memsize": str(8 * 1024 ** 3),
         "machdep.cpu.brand_string": "Intel(R) Core(TM) i5-8257U CPU @ 1.40GHz"}
AS = {"hw.optional.arm64": "1", "sysctl.proc_translated": "0", "hw.memsize": str(16 * 1024 ** 3)}


# ---- probe ----
def test_probe_intel_mac():
    info = mp.probe(system="Darwin", machine="x86_64", runner=fake_sysctl(INTEL), mac_ver="12.7.6")
    assert info["mac"] and info["intel"] and info["hardware"] == "x64" and not info["rosetta"]
    assert info["memGb"] == 8 and info["macos"] == "12.7.6" and "i5" in info["cpu"]


def test_probe_apple_silicon_native_and_rosetta():
    native = mp.probe(system="Darwin", machine="arm64", runner=fake_sysctl(AS), mac_ver="14.5")
    assert native["hardware"] == "arm64" and native["process"] == "arm64" and not native["rosetta"] and not native["intel"]
    ros = mp.probe(system="Darwin", machine="x86_64", runner=fake_sysctl({**AS, "sysctl.proc_translated": "1"}), mac_ver="14.5")
    assert ros["hardware"] == "arm64" and ros["process"] == "x64" and ros["rosetta"] and not ros["intel"]
    # Old Intel macOS without the arm64 sysctl key at all
    old = mp.probe(system="Darwin", machine="x86_64", runner=fake_sysctl({}), mac_ver="12.0")
    assert old["intel"] and old["hardware"] == "x64"


def test_probe_non_mac_never_calls_sysctl():
    def boom(*a, **k):
        raise AssertionError("no sysctl off macOS")
    info = mp.probe(system="Windows", machine="AMD64", runner=boom)
    assert info == {**info, "mac": False, "hardware": "x64", "intel": False}


# ---- Java arch choice ----
@pytest.mark.parametrize("hw,mc,major,expect", [
    ("x64", "1.21.11", 21, ("x64", True)),
    ("x64", "1.18.2", 17, ("x64", True)),
    ("x64", "26.3", 25, ("x64", True)),
    ("arm64", "1.21.11", 21, ("arm64", False)),
    ("arm64", "1.19", 17, ("arm64", False)),
    ("arm64", "26.1", 25, ("arm64", False)),
    ("arm64", "1.18.2", 17, ("x64", True)),   # LWJGL 3.2: Rosetta Java
    ("arm64", "1.16.5", 8, ("x64", True)),
])
def test_java_arch_for(hw, mc, major, expect):
    assert jd.java_arch_for(mc, major, {"mac": True, "hardware": hw}) == expect


def test_java_arch_for_non_mac_uses_host():
    assert jd.java_arch_for("1.21.11", 21, {"mac": False, "hardware": "x64"}) == ("x64", False)


def test_adoptium_url_uses_x64_and_aarch64():
    assert jd.api_url(21, "mac", "x64") == ("https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64"
                                            "&image_type=jre&os=mac&vendor=eclipse")
    assert "architecture=aarch64" in jd.api_url(17, "mac", "arm64")
    assert jd.adoptium_os("Darwin") == "mac" and jd.adoptium_os("Linux") == "linux"


def test_select_java_prefers_and_enforces_arch():
    javas = [{"path": "/arm21", "major": 21, "arch": "arm64"}, {"path": "/x17", "major": 17, "arch": "x64"},
             {"path": "/x21", "major": 21, "arch": "x64"}]
    assert javamod.select_java(21, javas, arch="x64", strict=True)[0] == "/x21"
    assert javamod.select_java(21, javas, arch="arm64")[0] == "/arm21"
    assert javamod.select_java(17, javas, arch="x64", strict=True)[0] == "/x17"
    only_arm = [{"path": "/arm21", "major": 21, "arch": "arm64"}]
    path, err = javamod.select_java(21, only_arm, arch="x64", strict=True)
    assert path is None and "Java 21" in err
    assert javamod.select_java(21, only_arm, arch="x64", strict=False)[0] == "/arm21"
    assert javamod.select_java(17, javas)[0] == "/x17"  # old behaviour without arch


def test_java_arch_reads_release_file(tmp_path):
    home = tmp_path / "jdk" / "Contents" / "Home"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "java").write_text("")
    (home / "release").write_text('JAVA_VERSION="21.0.5"\nOS_ARCH="x86_64"\n')
    assert javamod.java_arch(str(home / "bin" / "java")) == "x64"
    (home / "release").write_text('JAVA_VERSION="21.0.5"\nOS_ARCH="aarch64"\n')
    assert javamod.java_arch(str(home / "bin" / "java")) == "arm64"


def test_runtime_javas_are_candidates(tmp_path):
    java = tmp_path / "runtime" / "temurin-21-x64" / "jdk-21.0.5+11-jre" / "Contents" / "Home" / "bin" / "java"
    java.parent.mkdir(parents=True)
    java.write_text("")
    assert jd.installed_runtimes(tmp_path) == [str(java)]
    found = javamod.candidate_javas(which=lambda _: None, mac=False, runtime=lambda: jd.installed_runtimes(tmp_path))
    assert found[0] == str(java)


# ---- Temurin download ----
def _jre_tar(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class _Resp(io.BytesIO):
    headers = {}


def test_download_temurin_verifies_and_unpacks(tmp_path):
    blob = _jre_tar({"jdk-21.0.5+11-jre/Contents/Home/bin/java": b"#!/bin/sh\n",
                     "jdk-21.0.5+11-jre/Contents/Home/release": b'OS_ARCH="x86_64"\n'})
    urls = []
    assets = [{"binary": {"package": {"link": "https://github.com/adoptium/x.tar.gz", "name": "OpenJDK21U-jre_x64_mac.tar.gz",
                                      "checksum": hashlib.sha256(blob).hexdigest(), "size": len(blob)}},
               "version": {"openjdk_version": "21.0.5+11"}}]
    java = jd.download_temurin(tmp_path, 21, "x64", system="Darwin", fetch=lambda u: urls.append(u) or assets,
                               opener=lambda url, timeout: _Resp(blob))
    assert "architecture=x64" in urls[0] and "os=mac" in urls[0]
    assert java.endswith("temurin-21-x64/jdk-21.0.5+11-jre/Contents/Home/bin/java") and Path(java).is_file()
    assert javamod.java_arch(java) == "x64"
    meta = json.loads((tmp_path / "runtime" / "temurin-21-x64" / "vanta-runtime.json").read_text())
    assert meta["arch"] == "x64" and meta["os"] == "mac"


def test_download_temurin_rejects_bad_hash_and_traversal(tmp_path):
    blob = _jre_tar({"jre/bin/java": b"x"})
    bad = [{"binary": {"package": {"link": "https://x/y.tar.gz", "name": "y.tar.gz", "checksum": "0" * 64}}}]
    with pytest.raises(RuntimeError, match="SHA-256"):
        jd.download_temurin(tmp_path, 17, "x64", system="Darwin", fetch=lambda u: bad, opener=lambda url, timeout: _Resp(blob))
    evil = _jre_tar({"../../escape": b"x"})
    pkg = [{"binary": {"package": {"link": "https://x/y.tar.gz", "name": "y.tar.gz", "checksum": hashlib.sha256(evil).hexdigest()}}}]
    with pytest.raises(RuntimeError, match="Unsafe"):
        jd.download_temurin(tmp_path, 17, "x64", system="Darwin", fetch=lambda u: pkg, opener=lambda url, timeout: _Resp(evil))
    assert not (tmp_path.parent / "escape").exists()
    assert not (tmp_path / "runtime" / "temurin-17-x64").exists()
    with pytest.raises(RuntimeError):
        jd.pick_package([{"binary": {"package": {"link": "http://insecure/x.tar.gz", "name": "x.tar.gz", "checksum": "a"}}}])


def test_play_on_intel_mac_without_java_starts_x64_download(tmp_path, monkeypatch):
    (tmp_path / "1.21.11.json").write_text(json.dumps({"javaVersion": {"majorVersion": 21}}))
    monkeypatch.setattr(mp, "current", lambda: {"mac": True, "hardware": "x64", "intel": True})
    monkeypatch.setattr(javamod, "list_javas", lambda **k: [])
    started = []
    monkeypatch.setattr(jd, "start", lambda d, major, arch: started.append((major, arch)) or {"state": "running", "percent": 0})
    monkeypatch.delenv("VANTA_AUTO_JAVA", raising=False)
    path, msg = launch.java_for_instance(tmp_path, "1.21.11")
    assert path is None and started == [(21, "x64")] and "Intel (x64)" in msg and "Temurin" in msg
    monkeypatch.setenv("VANTA_AUTO_JAVA", "0")
    started.clear()
    assert launch.java_for_instance(tmp_path, "1.21.11")[0] is None and started == []


def test_play_on_apple_silicon_old_mc_needs_x64_java(tmp_path, monkeypatch):
    (tmp_path / "1.18.2.json").write_text(json.dumps({"javaVersion": {"majorVersion": 17}}))
    monkeypatch.setattr(mp, "current", lambda: {"mac": True, "hardware": "arm64", "intel": False})
    monkeypatch.setattr(javamod, "list_javas", lambda **k: [{"path": "/arm17", "major": 17, "arch": "arm64"},
                                                             {"path": "/x17", "major": 17, "arch": "x64"}])
    assert launch.java_for_instance(tmp_path, "1.18.2") == ("/x17", "")


# ---- LWJGL natives ----
LIBS_121 = [
    {"name": "org.lwjgl:lwjgl:3.3.3", "rules": None},
    {"name": "org.lwjgl:lwjgl:3.3.3:natives-macos", "rules": [{"action": "allow", "os": {"name": "osx"}}]},
    {"name": "org.lwjgl:lwjgl:3.3.3:natives-macos-arm64", "rules": [{"action": "allow", "os": {"name": "osx"}}]},
    {"name": "org.lwjgl:lwjgl:3.3.3:natives-windows", "rules": [{"action": "allow", "os": {"name": "windows"}}]},
]


def _mac_libs(hardware, monkeypatch):
    monkeypatch.setattr(install, "_os_name", lambda: "osx")
    return [lib["name"].split(":")[-1] for lib in LIBS_121
            if install._rules_allow(lib["rules"]) and install.library_wanted(lib, hardware=hardware)]


def test_intel_mac_gets_only_x64_macos_natives(monkeypatch):
    assert _mac_libs("x64", monkeypatch) == ["3.3.3", "natives-macos"]


def test_apple_silicon_keeps_both_natives(monkeypatch):
    assert _mac_libs("arm64", monkeypatch) == ["3.3.3", "natives-macos", "natives-macos-arm64"]


def test_natives_filter_ignores_other_os():
    lib = {"name": "org.lwjgl:lwjgl:3.3.3:natives-macos-arm64"}
    assert install.library_wanted(lib, os_name="windows", hardware="x64")


def test_pre119_classifier_natives_use_macos_key(monkeypatch):
    monkeypatch.setattr(install, "_os_name", lambda: "osx")
    lib = {"name": "org.lwjgl:lwjgl:3.2.2", "natives": {"osx": "natives-macos", "windows": "natives-windows"},
           "downloads": {"classifiers": {"natives-macos": {"path": "l-macos.jar", "url": "https://x/m"},
                                         "natives-windows": {"path": "l-win.jar", "url": "https://x/w"}}}}
    assert install.native_classifier(lib)["path"] == "l-macos.jar"


# ---- JVM flags ----
def test_mac_jvm_flags_intel():
    cmd = ["java", "-Dvanta.clientJar=/c.jar", "-Xms4G", "-Xmx4G", "-XstartOnFirstThread", "-cp", "x", "Main"]
    out = launch.mac_jvm_flags(cmd, {"mac": True, "hardware": "x64"}, 4)
    assert out[:3] == ["java", "-Dvanta.clientJar=/c.jar", "-XstartOnFirstThread"]
    assert out.count("-XstartOnFirstThread") == 1 and "-XX:+UseG1GC" in out and "-XX:MaxGCPauseMillis=130" in out
    assert launch.mac_jvm_flags(cmd, {"mac": False}, 4) == cmd
    custom = launch.mac_jvm_flags(cmd[:2] + ["-XX:+UseZGC"] + cmd[2:], {"mac": True, "hardware": "x64"}, 4)
    assert "-XX:+UseG1GC" not in custom


def test_user_gc_replaces_vanta_g1_and_arm_flags_dropped_on_intel():
    cmd = launch.mac_jvm_flags(["java", "-Dvanta.clientJar=/c.jar", "-Xmx4G", "Main"], {"mac": True}, 4)
    merged = launch.merge_user_jvm_args(cmd, ["-XX:+UseShenandoahGC"])
    assert "-XX:+UseG1GC" not in merged and "-XX:+UseShenandoahGC" in merged and "-XstartOnFirstThread" in merged
    assert launch.drop_foreign_flags(["-XX:+UseSVE=1", "-XX:+UseLSE", "-Xss2M"], "x64") == ["-Xss2M"]
    assert launch.drop_foreign_flags(["-XX:+UseLSE"], "arm64") == ["-XX:+UseLSE"]


# ---- Intel defaults ----
@pytest.mark.parametrize("mem,intel,ram", [(8, True, 3), (16, True, 4), (32, True, 6), (8, False, 4), (64, False, 6), (0, True, 4)])
def test_recommended_ram(mem, intel, ram):
    assert mp.recommended_ram_gb(mem, intel_mac=intel) == ram


def test_intel_igpu_detection():
    js = json.dumps({"SPDisplaysDataType": [{"sppci_model": "Intel Iris Plus Graphics 645"}]})
    assert mp.is_intel_igpu(mp.parse_displays(js))
    assert mp.is_intel_igpu(["Intel UHD Graphics 630"])
    assert not mp.is_intel_igpu(["Intel UHD Graphics 630", "AMD Radeon Pro 5500M"])
    assert not mp.is_intel_igpu(["Apple M2"]) and not mp.is_intel_igpu([])
    assert mp.parse_displays("not json") == []


def test_new_instance_options_on_intel_igpu(tmp_path, monkeypatch):
    from vanta.launcher.minecraft import instances
    monkeypatch.setattr(mp, "intel_igpu_only", lambda: True)
    text = instances.ensure_options(tmp_path / "a").read_text()
    assert "renderDistance:8" in text and "graphicsMode:0" in text and "toggleUse:false" in text
    monkeypatch.setattr(mp, "intel_igpu_only", lambda: False)
    assert "renderDistance" not in instances.ensure_options(tmp_path / "b").read_text()


def test_fast_preset_has_no_shaders_and_trailer_warns_on_intel(tmp_path, monkeypatch):
    from vanta.launcher.minecraft import instances
    assert "iris" not in [slug for slug, _ in instances.preset_mod_list("fast")]
    monkeypatch.setattr(mp, "intel_igpu_only", lambda: True)
    from vanta.launcher.ui.app import VantaApp
    app = VantaApp(data_dir=tmp_path)
    rows = {p["id"]: p for p in app.instance_presets()["presets"]}
    assert "Intel integrated graphics" in rows["trailer"]["warning"] and rows["fast"]["warning"] == ""
