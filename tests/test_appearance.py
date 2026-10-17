from pathlib import Path

from vanta.launcher.ui.app import VantaApp


def test_appearance_defaults_validate_and_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    app = VantaApp()
    r = app.ui_appearance()
    assert r["appearance"] == {"accent": "violet", "text": "normal", "contrast": "normal", "motion": "normal", "tour": "new"}
    assert app.ui_appearance({"accent": "orchid", "text": "large"})["appearance"]["accent"] == "orchid"
    assert not app.ui_appearance({"accent": "green"})["ok"]
    assert not app.ui_appearance({"evil": "x"})["ok"]
    again = VantaApp().ui_appearance()["appearance"]
    assert again["accent"] == "orchid" and again["text"] == "large"
    assert set(r["options"]["accent"]) == {"violet", "amethyst", "lavender", "orchid", "indigo", "grape"}


def test_ui_has_appearance_palette_tour():
    root = Path(__file__).resolve().parents[1]
    html = (root / "vanta/launcher/ui/index.html").read_text(encoding="utf-8")
    css = (root / "assets/style.css").read_text(encoding="utf-8")
    for needle in ('id="accent-swatches"', 'id="palette"', 'id="tour"', "/api/appearance", 'class="skip-link"', "aria-current"):
        assert needle in html
    for key in ("amethyst", "lavender", "orchid", "indigo", "grape"):
        assert f'[data-accent="{key}"]' in css
