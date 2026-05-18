"""Tests for the archive.org scraper. Uses an injected http_get so we never
hit the network."""

from __future__ import annotations

import json

from endless_library.config import ScrapersCfg
from endless_library.domain.models import SearchQuery
from endless_library.scrapers.archive_curl import ArchiveOrgCurl


def _q(**kw) -> SearchQuery:
    base = dict(
        title="Pride and Prejudice",
        author="Jane Austen",
        isbn13=None,
        format_priority=("epub", "azw3", "mobi", "pdf"),
        language="en",
    )
    base.update(kw)
    return SearchQuery(**base)


_SEARCH_BODY = json.dumps(
    {
        "response": {
            "numFound": 3,
            "docs": [
                {
                    "identifier": "austen-pride-and-prejudice",
                    "title": "Pride and Prejudice",
                    "creator": "Jane Austen",
                    "year": "1813",
                    "format": ["EPUB", "Text PDF", "DjVuTXT"],
                },
                {
                    "identifier": "borrowable-private-item",
                    "title": "Pride and Prejudice",
                    "creator": "Jane Austen",
                    "format": ["LCP Encrypted EPUB"],
                },
                {
                    "identifier": "no-known-format",
                    "title": "Pride and Prejudice",
                    "creator": "Jane Austen",
                    "format": ["DjVuTXT", "JSON"],
                },
            ],
        }
    }
).encode()


def _http_get_factory(responses: dict[str, bytes]):
    """Build a fake http_get that maps URL substrings to (status, body)."""

    def get(url: str):
        for needle, body in responses.items():
            if needle in url:
                return 200, body
        return 404, b""

    return get


def test_search_parses_three_candidates_skips_unmappable_format():
    s = ArchiveOrgCurl(
        ScrapersCfg(),
        http_get=_http_get_factory(
            {
                "advancedsearch.php": _SEARCH_BODY,
            }
        ),
    )
    hits = s.search(_q())
    # All three docs come back as Candidates (the format-mapping doesn't filter
    # the Candidate itself, only the format string within it).
    assert len(hits) == 3
    assert hits[0].provider == "archive"
    assert hits[0].title == "Pride and Prejudice"
    assert hits[0].author == "Jane Austen"
    assert hits[0].year == 1813
    assert hits[0].format == "epub"  # preferred from format_priority
    # LCP-only candidate gets no format because LCP -> None in the map
    assert hits[1].format is None
    # DjVuTXT/JSON have no mapping
    assert hits[2].format is None


def test_search_handles_empty_response():
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (200, b'{"response":{"docs":[]}}'))
    assert s.search(_q()) == []


def test_search_returns_empty_on_500():
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (500, b"boom"))
    assert s.search(_q()) == []


def test_search_returns_empty_on_bad_json():
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (200, b"not json"))
    assert s.search(_q()) == []


def test_format_priority_picks_epub_when_available():
    """If both EPUB and PDF are listed and the user prefers epub, epub wins."""
    body = json.dumps(
        {
            "response": {
                "docs": [
                    {
                        "identifier": "x",
                        "title": "X",
                        "creator": "A",
                        "format": ["Text PDF", "EPUB", "Image PDF"],
                    }
                ]
            }
        }
    ).encode()
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (200, body))
    hits = s.search(_q(format_priority=("epub", "pdf")))
    assert hits[0].format == "epub"
    hits = s.search(_q(format_priority=("pdf", "epub")))
    assert hits[0].format == "pdf"


def test_creator_list_is_flattened():
    body = json.dumps(
        {
            "response": {
                "docs": [
                    {
                        "identifier": "y",
                        "title": "Y",
                        "creator": ["Jane Austen", "Editor X"],
                        "format": ["EPUB"],
                    }
                ]
            }
        }
    ).encode()
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (200, body))
    hits = s.search(_q())
    assert hits[0].author == "Jane Austen"


# --------------- resolve_cdn ---------------


_FILES_BODY = json.dumps(
    {
        "result": [
            {"name": "thumb.jpg", "format": "Item Tile", "size": "31107"},
            {
                "name": "book.epub",
                "format": "EPUB",
                "size": "500000",
                # not private — should be picked
            },
            {
                "name": "book_lcp.epub",
                "format": "LCP Encrypted EPUB",
                "size": "500000",
            },
            {
                "name": "book.pdf",
                "format": "Text PDF",
                "size": "1500000",
            },
            {
                "name": "book_private.epub",
                "format": "EPUB",
                "private": "true",
                "size": "100",
            },
        ]
    }
).encode()


def _cand(**kw):
    from endless_library.domain.models import Candidate

    base = dict(
        provider="archive",
        md5=None,
        title="Pride and Prejudice",
        author="Jane Austen",
        language="en",
        format="epub",
        filesize_bytes=None,
        year=1813,
        publisher=None,
        edition_hints="",
        detail_url="https://archive.org/details/x",
        raw={"identifier": "test-id"},
    )
    base.update(kw)
    return Candidate(**base)


def test_resolve_cdn_picks_clean_epub_over_lcp_and_private():
    s = ArchiveOrgCurl(
        ScrapersCfg(),
        http_get=_http_get_factory(
            {
                "/metadata/": _FILES_BODY,
            }
        ),
    )
    handle = s.resolve_cdn(_cand())
    assert handle is not None
    assert "book.epub" in handle.url
    assert "lcp" not in handle.url.lower()
    assert "private" not in handle.url.lower()
    assert handle.expected_filename == "book.epub"


def test_resolve_cdn_falls_back_to_pdf_when_no_epub():
    body = json.dumps(
        {
            "result": [
                {"name": "book.pdf", "format": "Text PDF", "size": "1500000"},
                {"name": "scan.djvu.txt", "format": "DjVuTXT", "size": "30000"},
            ]
        }
    ).encode()
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (200, body))
    handle = s.resolve_cdn(_cand())
    assert handle is not None
    assert handle.expected_filename == "book.pdf"


def test_resolve_cdn_returns_none_when_no_usable_file():
    body = json.dumps(
        {
            "result": [
                {"name": "thumb.jpg", "format": "Item Tile"},
                {"name": "_meta.xml", "format": "Metadata"},
                {"name": "x.djvu.txt", "format": "DjVuTXT"},
            ]
        }
    ).encode()
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (200, body))
    handle = s.resolve_cdn(_cand())
    assert handle is None


def test_resolve_cdn_returns_none_when_identifier_missing():
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (200, _FILES_BODY))
    handle = s.resolve_cdn(_cand(raw={}))
    assert handle is None


def test_resolve_cdn_returns_none_on_500():
    s = ArchiveOrgCurl(ScrapersCfg(), http_get=lambda _u: (502, b""))
    assert s.resolve_cdn(_cand()) is None


def test_search_url_excludes_borrow_only_collection():
    """Construct a search and verify the query string blocks inlibrary items."""
    captured: list[str] = []

    def cap(url: str):
        captured.append(url)
        return 200, b'{"response":{"docs":[]}}'

    s = ArchiveOrgCurl(ScrapersCfg(), http_get=cap)
    s.search(_q())
    assert captured, "search did not call http_get"
    # quote_plus encodes "-collection:inlibrary" -> "-collection%3Ainlibrary"
    assert "inlibrary" in captured[0]
    # And we asked for the format field
    assert "format" in captured[0]
