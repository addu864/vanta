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
