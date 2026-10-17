from pathlib import Path

from vanta.shared import macwindow


def test_macwindow_never_opens_browser(monkeypatch):
    started = []
    monkeypatch.setattr("vanta.launcher.ui.server.serve", lambda: started.append(1))
    monkeypatch.setenv("VANTA_OPEN_BROWSER", "1")
    macwindow.start_server()
    import os
    assert os.environ["VANTA_OPEN_BROWSER"] == "0"
    src = Path(macwindow.__file__).read_text()
    assert "webbrowser" not in src and "/usr/bin/open" not in src


def test_launcher_script_uses_native_window():
    script = (Path(__file__).resolve().parents[1] / "packaging/macos/Vanta").read_text()
    assert "vanta.shared.macwindow" in script
    assert "VANTA_OPEN_BROWSER=1" not in script and "/usr/bin/open" not in script
