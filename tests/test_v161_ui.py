from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "vanta/launcher/ui/index.html").read_text(encoding="utf-8")


def test_log_search_and_filters():
    for needle in ('id="log-filter"', 'id="log-errors"', 'id="log-count"', "function renderLog", "logShown"):
        assert needle in HTML
