"""Welib: search selects title anchors only, resolve_cdn prefers IPFS, skips covers."""

from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.scrapers.welib_curl import (
    WelibCurl,
    _is_book_payload_url,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "welib"


def _cfg():
    return ScrapersCfg(
        order=["welib_curl"],
        enabled={"welib_curl": True},
        flaresolverr_url="http://x:8191/v1",
        format_priority=["epub"],
        language="en",
        request_delay_seconds=0,
        slow_download_timeout_seconds=2,
    )


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://welib-public.org/covers/proxy?url=cover.jpg", False),
        ("https://welib-premium.org/path/book.epub", True),
        ("https://ipfs.eth.aragon.network/ipfs/bafy123?filename=book.epub", True),
        ("https://ipfs.io/ipfs/bafy123/book.pdf", True),
        ("https://ipfs.io/ipfs/bafy123", False),  # no ext + no filename query
        ("https://example.com/anything.epub", True),
    ],
)
def test_is_book_payload_url(url: str, expected: bool):
    assert _is_book_payload_url(url) is expected


def test_search_picks_title_anchors_not_image_or_aside(monkeypatch):
    html = (FIX / "search.html").read_text()
    s = WelibCurl(_cfg(), http_get=lambda u, *, headers: (200, html))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    out = s.search(
        SearchQuery(
            title="Pragmatic", author=None, isbn13=None, format_priority=("epub",), language="en"
        )
    )
    md5s = [c.md5 for c in out]
    # 2 main results, sidebar aside must NOT appear
    assert "a" * 32 in md5s and "b" * 32 in md5s
    assert "d" * 32 not in md5s
    titles = [c.title for c in out]
    assert any("Pragmatic Programmer" in (t or "") for t in titles)


def test_resolve_cdn_prefers_ipfs_over_cover(monkeypatch):
    html = (FIX / "detail_ready.html").read_text()
    s = WelibCurl(_cfg(), http_get=lambda u, *, headers: (200, html))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    # Stub the real HEAD probe so the test stays hermetic
    monkeypatch.setattr(WelibCurl, "_ipfs_reachable", lambda self, u, timeout=8.0: True)
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
    assert "ipfs" in h.url
    assert "covers" not in h.url


def test_resolve_cdn_returns_none_when_only_covers(monkeypatch):
    html = (
        '<html><body><img src="https://s2.welib-public.org/covers/proxy?url=x.jpg"></body></html>'
    )
    s = WelibCurl(_cfg(), http_get=lambda u, *, headers: (200, html))
    monkeypatch.setattr("time.sleep", lambda *_: None)
    c = Candidate(
        provider="welib",
        md5="a" * 32,
        title="x",
        author=None,
        language=None,
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="https://welib.org/md5/" + "a" * 32,
    )
    # No IPFS, no slow_download anchors → slow_download polling times out
    handle = s.resolve_cdn(c)
    assert handle is None
