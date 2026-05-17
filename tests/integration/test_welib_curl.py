from __future__ import annotations

from pathlib import Path

from endless_library.config import ScrapersCfg
from endless_library.domain.models import SearchQuery
from endless_library.scrapers.welib_curl import WelibCurl

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "welib"


def _cfg():
    return ScrapersCfg(order=["welib_curl"], enabled={"welib_curl": True})


def test_search_and_resolve(monkeypatch):
    search = (FIX / "search.html").read_text()
    detail = (FIX / "detail_ready.html").read_text()

    def fake_get(url, *, headers):
        if "/search" in url:
            return 200, search
        return 200, detail

    s = WelibCurl(_cfg(), http_get=fake_get)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    results = s.search(
        SearchQuery(
            title="The Pragmatic Programmer",
            author=None,
            isbn13=None,
            format_priority=("epub",),
            language="en",
        )
    )
    assert results
    handle = s.resolve_cdn(results[0])
    assert handle is not None
    assert "welib-premium.org" in handle.url
