def test_skin_library_loads_after_startup():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert '<script src="/assets/vendor/skinview3d.bundle.js">' not in s
    assert "function loadSkinLib" in s and "requestIdleCallback" in s
