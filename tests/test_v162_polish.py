from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "vanta/launcher/ui/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "assets/style.css").read_text(encoding="utf-8")


def test_api_never_throws_and_reconnects():
    start = HTML.index("async function api(")
    body = HTML[start:HTML.index("function skeleton(", start)]
    assert "catch (err) { connLost();" in body and "friendlyHttp(response.status)" in body
    assert 'id="conn-banner"' in HTML and ".conn-banner" in CSS


def test_skeletons_and_empty_states():
    assert "Loading…</p>" not in HTML.split("<script>")[0]  # static placeholders are skeletons now
    for needle in ('class="skel-list"', 'class="skel-card"', "function emptyState(", "No screenshots yet", "Couldn't reach Modrinth"):
        assert needle in HTML
    assert ".skel {" in CSS and ".empty-state {" in CSS and ':root[data-motion="reduce"] .skel' in CSS


def test_cape_preview_is_preview_only():
    for needle in ('id="cape-pick"', 'id="cape-back"', 'id="cape-file"', "function drawCape(", "loadCape(img", "Preview only."):
        assert needle in HTML
    start = HTML.index("function drawCape(")
    block = HTML[start:HTML.index("// ---- Popular servers", start)]
    assert "post(" not in block and "fetch(" not in block  # nothing is uploaded or written to the game


def test_launcher_accessibility_names():
    for needle in ('id="mb-type" aria-label="Type"', 'id="mb-sort" aria-label="Sort by"', 'id="skin-file" aria-label=',
                   'id="skin-import-model" aria-label=', 'aria-label="Which log"', 'id="palette-list" role="listbox" aria-label="Matches"',
                   '"Arm model for "'):
        assert needle in HTML
    assert 'role="tablist"><button type="button" class="seg-btn' not in HTML
    assert 'card.setAttribute("role", "button");\n      card.setAttribute("aria-label", "Select and apply' not in HTML


def test_instance_name_is_saved_cleaned_and_listed(tmp_path, monkeypatch):
    import json
    from vanta.launcher.minecraft import versions
    from vanta.launcher.ui.app import VantaApp
    monkeypatch.setenv("VANTA_DATA_DIR", str(tmp_path))
    app = VantaApp()
    folder = tmp_path / "inst"
    folder.mkdir()
    (folder / "vanta-settings.json").write_text(json.dumps({"ramGb": 4, "jvmArgs": "-Xss2m", "other": 1}))
    monkeypatch.setattr(app, "_instance_dir", lambda body: (folder, None))
    r = app.save_instance_settings({"ramGb": 6, "jvmArgs": "-Xss2m", "label": "  My <b>SMP</b>\x00 world " + "x" * 60})
    assert r["ok"] and r["settings"]["label"].startswith("My bSMP/b world") and len(r["settings"]["label"]) == 40
    data = json.loads((folder / "vanta-settings.json").read_text())
    assert data["other"] == 1 and data["ramGb"] == 6  # other keys kept
    app.save_instance_settings({"ramGb": 6, "jvmArgs": ""})  # no label key: name kept
    assert versions.instance_label(folder) == r["settings"]["label"]
    assert 'id="inst-label"' in HTML and "version.label ?" in HTML
