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


# ============ ipfs reachability uses range-GET (PR #32 root cause A) ============


class _FakeResp:
    """Minimal stand-in for httpx.Response so we can simulate gateway behavior."""
    def __init__(self, status_code: int, body: bytes = b""):
        self.status_code = status_code
        self._body = body
        self.headers = {}
    def iter_content(self, chunk_size: int = 1024):
        yield self._body
    def close(self):
        pass


def test_ipfs_reachable_treats_504_as_unreachable(monkeypatch):
    """HEAD-probing missed ipfs.io 504s because the CDN returns 200 on HEAD.
    Range-GET surfaces the real gateway health: 504 must return False so
    the caller's loop falls through to the next of the ~30 gateways."""
    s = WelibCurl(_cfg())

    captured: dict = {}
    class _C:
        def get(self, url, *, headers=None, allow_redirects=False, verify=False, stream=False):
            captured["url"] = url
            captured["headers"] = headers
            captured["method"] = "GET"
            return _FakeResp(504, body=b"<html>gateway timeout</html>")
        def head(self, *a, **kw):
            captured["method"] = "HEAD"  # MUST NOT be called now
            return _FakeResp(200)

    monkeypatch.setattr("endless_library.scrapers.welib_curl.make_client", lambda *a, **kw: _C())

    ok = s._ipfs_reachable("https://ipfs.io/ipfs/bafy123/book.epub")
    assert ok is False, "504 gateway must be marked unreachable"
    assert captured.get("method") == "GET", "must use range-GET, not HEAD"
    assert captured.get("headers", {}).get("Range") == "bytes=0-1023"


def test_ipfs_reachable_accepts_206_partial(monkeypatch):
    """Spec-compliant gateways answer a Range request with 206 Partial Content.
    That's still 'reachable' — must return True."""
    s = WelibCurl(_cfg())

    class _C:
        def get(self, url, *, headers=None, allow_redirects=False, verify=False, stream=False):
            return _FakeResp(206, body=b"PK\x03\x04...")

    monkeypatch.setattr("endless_library.scrapers.welib_curl.make_client", lambda *a, **kw: _C())
    assert s._ipfs_reachable("https://gateway.example/ipfs/x") is True


def test_ipfs_reachable_accepts_200(monkeypatch):
    """Some gateways ignore Range and just return the whole body with 200.
    Still healthy."""
    s = WelibCurl(_cfg())

    class _C:
        def get(self, url, *, headers=None, allow_redirects=False, verify=False, stream=False):
            return _FakeResp(200, body=b"PK\x03\x04...")

    monkeypatch.setattr("endless_library.scrapers.welib_curl.make_client", lambda *a, **kw: _C())
    assert s._ipfs_reachable("https://gateway.example/ipfs/x") is True


def test_ipfs_reachable_treats_exception_as_unreachable(monkeypatch):
    """Network errors should never escape the probe — they just mean
    'try the next gateway'."""
    s = WelibCurl(_cfg())

    class _C:
        def get(self, *a, **kw):
            raise RuntimeError("connection reset")

    monkeypatch.setattr("endless_library.scrapers.welib_curl.make_client", lambda *a, **kw: _C())
    assert s._ipfs_reachable("https://gateway.example/ipfs/x") is False


# ============ denylist + HTML rejection (PR #33 root cause L1) ============


def test_ipfs_reachable_rejects_html_wrapper(monkeypatch):
    """ipfs.io now returns an HTML loader page (HTTP 200, text/html)
    instead of book bytes. Probe must reject this so the gateway loop
    moves on to the next of the ~30 listed gateways."""
    s = WelibCurl(_cfg())

    class _C:
        def get(self, url, *, headers=None, allow_redirects=False, verify=False, stream=False):
            r = _FakeResp(200, body=b"<!DOCTYPE html><html><head><title>IPFS Service Worker</title></head>")
            r.headers = {"Content-Type": "text/html; charset=utf-8"}
            return r

    monkeypatch.setattr("endless_library.scrapers.welib_curl.make_client", lambda *a, **kw: _C())
    # Patch FakeResp to expose headers attribute the new probe reads
    assert s._ipfs_reachable("https://anywhere.example/ipfs/x") is False


def test_ipfs_reachable_rejects_html_by_body_signature(monkeypatch):
    """Gateways that lie about Content-Type but still serve HTML must
    still be rejected based on body content."""
    s = WelibCurl(_cfg())

    class _C:
        def get(self, url, *, headers=None, allow_redirects=False, verify=False, stream=False):
            r = _FakeResp(200, body=b"<html><body>not a book</body></html>")
            # Deliberately lie about Content-Type
            r.headers = {"Content-Type": "application/octet-stream"}
            return r

    monkeypatch.setattr("endless_library.scrapers.welib_curl.make_client", lambda *a, **kw: _C())
    assert s._ipfs_reachable("https://liar.example/ipfs/x") is False


def test_ipfs_reachable_accepts_real_epub_payload(monkeypatch):
    """A gateway serving real ebook bytes (ZIP signature = PK\x03\x04
    for epubs) must still pass."""
    s = WelibCurl(_cfg())

    class _C:
        def get(self, url, *, headers=None, allow_redirects=False, verify=False, stream=False):
            r = _FakeResp(206, body=b"PK\x03\x04...some real epub bytes here")
            r.headers = {"Content-Type": "application/epub+zip"}
            return r

    monkeypatch.setattr("endless_library.scrapers.welib_curl.make_client", lambda *a, **kw: _C())
    assert s._ipfs_reachable("https://goodgw.example/ipfs/x") is True


def test_resolve_skips_ipfs_io_even_when_listed(monkeypatch):
    """ipfs.io is in _DEAD_GATEWAYS — must be filtered out of the gateway
    list even when welib includes it in /ipfs_downloads/md5: response.
    The next listed (non-ipfs.io) gateway should be probed instead."""
    from endless_library.scrapers.welib_curl import WelibCurl as _WC

    # Synthesize a /ipfs_downloads listing with ipfs.io first + a working
    # alternative second.
    listing_html = """
    <a href="https://ipfs.io/ipfs/bafy123/book.epub">ipfs.io</a>
    <a href="https://dweb.link/ipfs/bafy123/book.epub">dweb.link</a>
    <a href="https://ipfs.eth.aragon.network/ipfs/bafy123/book.epub">aragon</a>
    """

    probed: list[str] = []
    def _fake_get(url, *, headers=None):
        return (200, listing_html) if "/ipfs_downloads/md5:" in url else (404, "")

    s2 = _WC(_cfg(), http_get=_fake_get)
    def _reach(self, url, *, timeout=10.0):
        probed.append(url)
        # Pretend the first non-denylisted gateway is reachable.
        return True
    monkeypatch.setattr(_WC, "_ipfs_reachable", _reach)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    candidate = Candidate(
        provider="welib",
        md5="a" * 32,
        title="x",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="https://welib.org/md5/" + "a" * 32,
        raw={},
    )
    handle = s2.resolve_cdn(candidate)
    # The handle's URL must be one of the non-ipfs.io alternatives.
    assert handle is not None
    assert "ipfs.io" not in handle.url, f"ipfs.io leaked through denylist: {handle.url}"
    # And ipfs.io was never even probed
    assert not any("ipfs.io" in u for u in probed), f"ipfs.io probed despite denylist: {probed}"


def test_meta_refresh_skipped_when_url_is_ipfs_io(monkeypatch):
    """Even on the meta-refresh fallback path (when /ipfs_downloads/md5
    returns nothing), if the meta-refresh URL points at ipfs.io we
    must not return it."""
    from endless_library.scrapers.welib_curl import WelibCurl as _WC
    detail_html = """
    <meta http-equiv="refresh" content="0;url=https://ipfs.io/ipfs/bafy999/book.epub">
    """
    def _fake_get(url, *, headers=None):
        if "/ipfs_downloads/md5:" in url:
            return (404, "")
        if "/md5/" in url:
            return (200, detail_html)
        return (200, "")
    s = _WC(_cfg(), http_get=_fake_get)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    candidate = Candidate(
        provider="welib", md5="b" * 32, title="x", author=None, language="en",
        format="epub", filesize_bytes=None, year=None, publisher=None,
        edition_hints="", detail_url="https://welib.org/md5/" + "b" * 32, raw={},
    )
    handle = s.resolve_cdn(candidate)
    # We expect None because the only path led to an ipfs.io URL that the
    # denylist rejected. We don't want to silently return that URL.
    assert handle is None or "ipfs.io" not in handle.url
