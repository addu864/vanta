def test_crash_card_links_to_site_search():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert 'id="crash-help"' in s and "guides.html?q=" not in s.split('id="crash-help"')[1][:200]  # href built at runtime
    assert '"?q=" + encodeURIComponent(q)' in s


def test_conflict_fix_ui_and_route():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    assert "/api/instances/conflicts/fix" in s and "function runConflictFix" in s and "function openModsFor" in s
    from vanta.launcher.ui import server
    src = open(server.__file__, encoding="utf-8").read()
    assert '"/api/instances/conflicts/fix"' in src


def test_server_join_asks_about_mods_too():
    s = open("vanta/launcher/ui/index.html", encoding="utf-8").read()
    join = s[s.index("async function playServer"):s.index("function serverCard")]
    assert "checkConflicts: true" in join and "showConflicts(res, target)" in join and "conflictsOk: true" in join
