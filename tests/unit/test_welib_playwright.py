from __future__ import annotations

import pytest

from endless_library.scrapers.welib_playwright import (
    WelibPlaywright,
    _looks_like_book_response,
)


@pytest.mark.parametrize(
    "url,ct,expected",
    [
        ("https://welib-public.org/covers/proxy?x.jpg", "image/jpeg", False),
        ("https://welib-premium.org/path/book.epub", "application/epub+zip", True),
        ("https://anywhere/foo.pdf", "application/pdf", True),
        ("https://ipfs.io/ipfs/bafy?filename=x.epub", None, True),
        ("https://ipfs.io/ipfs/bafy", None, False),  # no ext, no filename
        ("https://example.com/api/x", "application/json", False),
        ("https://example.com/page", "text/html", False),
        ("https://example.com/some.bin", "application/octet-stream", True),
        ("https://example.com/cover.png", "image/png", False),
    ],
)
def test_looks_like_book_response(url, ct, expected):
    assert _looks_like_book_response(url, ct) is expected


def test_welib_playwright_in_registry():
    from endless_library.scrapers import registry

    assert "welib_playwright" in registry.available()


def test_welib_playwright_inherits_search():
    """search() is inherited from WelibCurl."""
    from endless_library.config import ScrapersCfg

    cfg = ScrapersCfg(flaresolverr_url="http://x:8191/v1")
    s = WelibPlaywright(
        cfg,
        http_get=lambda u, *, headers: (
            200,
            '<a href="/md5/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"></a>'
            '<a href="/md5/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">Pragmatic Programmer</a>',
        ),
    )
    from endless_library.domain.models import SearchQuery

    out = s.search(
        SearchQuery(title="x", author=None, isbn13=None, format_priority=("epub",), language="en")
    )
    assert len(out) == 1
    assert out[0].md5 == "a" * 32
