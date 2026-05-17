"""Welib parses real-shape DOM and uses /auto_download/ for resolve_cdn."""

from __future__ import annotations

from pathlib import Path

from endless_library.config import ScrapersCfg
from endless_library.domain.models import SearchQuery
from endless_library.scrapers.welib_curl import WelibCurl

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "welib"


def _cfg():
    return ScrapersCfg(
        order=["welib_curl"],
        enabled={"welib_curl": True},
        flaresolverr_url="http://x:8191/v1",
        format_priority=["epub"],
        language="en",
        request_delay_seconds=0,
    )


def test_parse_results_picks_book_card_titles(monkeypatch):
    html = (FIX / "search.html").read_text()

    def fake_get(url, *, headers):
        return 200, html

    s = WelibCurl(_cfg(), http_get=fake_get)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    out = s.search(
        SearchQuery(
            title="Pragmatic Programmer",
            author=None,
            isbn13=None,
            format_priority=("epub",),
            language="en",
        )
    )
    assert len(out) == 2
    assert out[0].md5 == "a" * 32
    # Title comes from the .book-title anchor, not the image one
    assert out[0].title and "Pragmatic" in out[0].title
    assert out[0].format == "epub"
    assert out[0].filesize_bytes and out[0].filesize_bytes > 1_000_000
    assert out[0].language == "en"


def test_resolve_cdn_picks_up_welib_url(monkeypatch):
    detail = (FIX / "detail_ready.html").read_text()

    def fake_get(url, *, headers):
        return 200, detail

    s = WelibCurl(_cfg(), http_get=fake_get)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    from endless_library.domain.models import Candidate

    c = Candidate(
        provider="welib",
        md5="a" * 32,
        title="x",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=2_000_000,
        year=2019,
        publisher=None,
        edition_hints="",
        detail_url="https://welib.org/md5/" + "a" * 32,
    )
    h = s.resolve_cdn(c)
    assert h is not None
    assert "welib-premium.org" in h.url
