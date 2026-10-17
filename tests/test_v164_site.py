import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_compare_page_linked_everywhere():
    page = (DOCS / "compare.html").read_text(encoding="utf-8")
    for t in ("Vanilla", "Fast", "Custom", "Trailer style", "Sodium", "Iris", '"FAQPage"'):
        assert t in page
    assert "compare.html" in (DOCS / "sitemap.xml").read_text(encoding="utf-8")
    for name in ("index.html", "guides.html", "news.html"):
        assert 'href="compare.html"' in (DOCS / name).read_text(encoding="utf-8"), name


def test_troubleshooting_search():
    g = (DOCS / "guides.html").read_text(encoding="utf-8")
    assert 'id="ts-search"' in g and g.count('class="faq-item glass"') >= 20
    assert "#ts-search" in (DOCS / "app.js").read_text(encoding="utf-8")


def test_lighter_pages():
    for page in DOCS.glob("*.html"):
        s = page.read_text(encoding="utf-8")
        assert not re.search(r'<img[^>]+src="[^"]*logo\.jpg"', s), page.name
        assert not re.search(r'<img[^>]+src="img/servers/[^"]+\.png"', s), page.name
    js = (DOCS / "app.js").read_text(encoding="utf-8")
    assert "IntersectionObserver" in js and "function loadReleases" in js
