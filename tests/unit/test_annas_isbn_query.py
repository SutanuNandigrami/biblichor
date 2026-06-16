"""Annas scraper must include ISBN in the search query when SearchQuery
carries one. Live evidence: searching by title+author alone, the row HTML
doesn't always carry the ISBN, so the parser can't extract it and the
scorer can't fire its 35-point ISBN match.

Verified live on 2026-06-02 against goodreads.com that book detail pages
expose ISBN via JSON-LD, AND on annas-archive.gl that searching `q={isbn}`
returns an Identifier line including that ISBN inside row HTML."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from endless_library.domain.models import SearchQuery
from endless_library.scrapers.annas_curl import AnnasArchiveCurl


def _make_scraper(captured_urls: list[str]):
    """Build an AnnasArchiveCurl that captures every URL `_get` would fetch."""

    def _fake_get(self, url: str) -> str | None:
        captured_urls.append(url)
        return ""  # empty HTML => parser yields no candidates, we don't care here
    sc = AnnasArchiveCurl.__new__(AnnasArchiveCurl)
    sc._get = _fake_get.__get__(sc, AnnasArchiveCurl)
    # Stub the bits the search method touches:
    sc.mirrors = type("M", (), {"current": "https://annas-archive.gl"})()
    sc.cfg = type("C", (), {
        "request_delay_seconds": 0.0,
        "max_retries": 0,
    })()
    sc.bucket = type("B", (), {"acquire": lambda *a, **kw: None, "release": lambda *a, **kw: None})()
    sc._last_request = 0.0
    sc._http_get = lambda *a, **kw: ""
    return sc


def test_annas_search_includes_isbn_in_query_when_present():
    captured: list[str] = []
    sc = _make_scraper(captured)
    q = SearchQuery(
        title="Fourth Wing",
        author="Rebecca Yarros",
        isbn13="9781649374042",
        format_priority=("epub",),
        language="en",
    )
    sc.search(q)
    assert captured, "no URL was fetched"
    parsed_q = parse_qs(urlparse(captured[0]).query).get("q", [""])[0]
    # All three tokens present
    assert "Fourth Wing" in parsed_q
    assert "Rebecca Yarros" in parsed_q
    assert "9781649374042" in parsed_q


def test_annas_search_omits_isbn_token_when_search_query_has_no_isbn():
    captured: list[str] = []
    sc = _make_scraper(captured)
    q = SearchQuery(
        title="Some Book",
        author="Author X",
        isbn13=None,
        format_priority=("epub",),
        language="en",
    )
    sc.search(q)
    assert captured
    parsed_q = parse_qs(urlparse(captured[0]).query).get("q", [""])[0]
    # Title and author present; no spurious ISBN-like token (13 digits)
    import re
    assert "Some Book" in parsed_q
    assert "Author X" in parsed_q
    assert not re.search(r"\b\d{13}\b", parsed_q)
