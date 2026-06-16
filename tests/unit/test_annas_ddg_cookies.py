"""Unit tests for DDGCookieCache + AnnasArchivePatchright._try_cookie_replay.

PR #38: cache the __ddg* cookies that patchright extracts after a
successful DDG challenge, replay them via plain httpx on subsequent
calls. Expected to drop ~80% of the per-book wallclock on cache-hit.
These tests cover the cache contract (TTL, persistence, invalidation)
and the replay path's failure modes without touching annas or
launching a browser.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from endless_library.config import Config
from endless_library.domain.models import Candidate
from endless_library.scrapers.annas_ddg_cookies import (
    DDGCookieCache,
)
from endless_library.scrapers.annas_patchright import (
    _CHROMIUM_UA,
    AnnasArchivePatchright,
)

# ============ DDGCookieCache ============


def test_set_then_get_returns_same_jar(tmp_path: Path):
    cache = DDGCookieCache(path=tmp_path / "c.json")
    cache.set("https://annas-archive.gl", {"__ddg1_": "abc"}, _CHROMIUM_UA)
    e = cache.get("https://annas-archive.gl")
    assert e is not None
    assert e.cookies == {"__ddg1_": "abc"}
    assert e.user_agent == _CHROMIUM_UA


def test_get_returns_none_when_expired(tmp_path: Path):
    cache = DDGCookieCache(path=tmp_path / "c.json")
    cache.set("https://annas-archive.gl", {"x": "y"}, _CHROMIUM_UA, ttl_seconds=0)
    # ttl_seconds=0 -> expires_at == now -> get() rejects on equality
    time.sleep(0.01)
    assert cache.get("https://annas-archive.gl") is None


def test_invalidate_removes_entry(tmp_path: Path):
    cache = DDGCookieCache(path=tmp_path / "c.json")
    cache.set("m", {"a": "b"}, _CHROMIUM_UA)
    cache.invalidate("m")
    assert cache.get("m") is None


def test_persistence_across_instances(tmp_path: Path):
    """File-backed cache survives process restart."""
    p = tmp_path / "c.json"
    DDGCookieCache(path=p).set("m", {"k": "v"}, _CHROMIUM_UA)
    # New instance reads same file
    e = DDGCookieCache(path=p).get("m")
    assert e is not None
    assert e.cookies == {"k": "v"}


def test_no_path_means_in_memory_only(tmp_path: Path):
    """Tests + sandboxed environments use path=None. Must still work."""
    cache = DDGCookieCache(path=None)
    cache.set("m", {"a": "b"}, _CHROMIUM_UA)
    assert cache.get("m") is not None


def test_corrupt_file_does_not_explode(tmp_path: Path):
    p = tmp_path / "c.json"
    p.write_text("{this is not json")
    cache = DDGCookieCache(path=p)
    # Should silently start fresh, not raise
    assert cache.get("m") is None
    # And should still be usable
    cache.set("m", {"a": "b"}, _CHROMIUM_UA)
    assert cache.get("m") is not None


def test_invalidate_persists_to_file(tmp_path: Path):
    p = tmp_path / "c.json"
    c1 = DDGCookieCache(path=p)
    c1.set("m", {"a": "b"}, _CHROMIUM_UA)
    c1.invalidate("m")
    # New instance must not see the invalidated entry
    assert DDGCookieCache(path=p).get("m") is None
    # File must reflect deletion
    data = json.loads(p.read_text())
    assert "m" not in data


def test_set_overwrites_existing_entry(tmp_path: Path):
    cache = DDGCookieCache(path=tmp_path / "c.json")
    cache.set("m", {"a": "1"}, "ua-a")
    cache.set("m", {"a": "2"}, "ua-b")
    e = cache.get("m")
    assert e is not None
    assert e.cookies == {"a": "2"}
    assert e.user_agent == "ua-b"


def test_multiple_mirrors_isolated(tmp_path: Path):
    cache = DDGCookieCache(path=tmp_path / "c.json")
    cache.set("gl", {"a": "1"}, _CHROMIUM_UA)
    cache.set("pk", {"b": "2"}, _CHROMIUM_UA)
    cache.invalidate("gl")
    assert cache.get("gl") is None
    assert cache.get("pk") is not None


# ============ AnnasArchivePatchright._try_cookie_replay ============


def _make_scraper(tmp_path: Path) -> AnnasArchivePatchright:
    """Build a scraper with an isolated cache file."""
    cache = DDGCookieCache(path=tmp_path / "c.json")
    return AnnasArchivePatchright(Config().scrapers, cookie_cache=cache)


def _md5_candidate(md5: str) -> Candidate:
    return Candidate(
        provider="annas",
        md5=md5,
        title="x",
        author=None,
        language=None,
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url=f"https://annas-archive.gl/md5/{md5}",
    )


def _httpx_response(status: int, body: str) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = body
    return r


def test_replay_misses_when_no_cached_entry(tmp_path: Path):
    s = _make_scraper(tmp_path)
    assert s._try_cookie_replay("https://annas-archive.gl/slow_download/x/0/0") is None


def test_replay_returns_handle_on_partner_anchor(tmp_path: Path):
    s = _make_scraper(tmp_path)
    s._cookie_cache.set(s.mirrors.current, {"__ddg1_": "z"}, _CHROMIUM_UA)
    html = (
        "<html><head><title>Anna's Archive</title></head><body>"
        '<a href="https://b4mcx2ml.net/d3/abc~xyz">📚 Download now</a>'
        "</body></html>"
    )
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    fake.get = MagicMock(return_value=_httpx_response(200, html))
    with patch.object(httpx, "Client", return_value=fake):
        handle = s._try_cookie_replay(f"{s.mirrors.current}/slow_download/abc/0/0")
    assert handle is not None
    assert handle.url == "https://b4mcx2ml.net/d3/abc~xyz"
    # Cache stays valid on success
    assert s._cookie_cache.get(s.mirrors.current) is not None


def test_replay_returns_handle_on_partner_d3_url_fallback(tmp_path: Path):
    """If the 'Download now' anchor isn't found we fall back to the
    first /d3/ URL on the page."""
    s = _make_scraper(tmp_path)
    s._cookie_cache.set(s.mirrors.current, {"__ddg1_": "z"}, _CHROMIUM_UA)
    html = (
        "<html><head><title>fine</title></head><body>"
        'metadata: <span data-url="https://mirror.example/d3/abc~123"></span>'
        "</body></html>"
    )
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    fake.get = MagicMock(return_value=_httpx_response(200, html))
    with patch.object(httpx, "Client", return_value=fake):
        handle = s._try_cookie_replay(f"{s.mirrors.current}/slow_download/abc/0/0")
    assert handle is not None
    assert handle.url == "https://mirror.example/d3/abc~123"


def test_replay_invalidates_on_ddg_title(tmp_path: Path):
    s = _make_scraper(tmp_path)
    s._cookie_cache.set(s.mirrors.current, {"__ddg1_": "z"}, _CHROMIUM_UA)
    html = "<html><head><title>DDoS-Guard</title></head></html>"
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    fake.get = MagicMock(return_value=_httpx_response(200, html))
    with patch.object(httpx, "Client", return_value=fake):
        out = s._try_cookie_replay(f"{s.mirrors.current}/slow_download/abc/0/0")
    assert out is None
    # The stale cookie MUST have been evicted so we don't loop
    assert s._cookie_cache.get(s.mirrors.current) is None


def test_replay_invalidates_on_non_200(tmp_path: Path):
    s = _make_scraper(tmp_path)
    s._cookie_cache.set(s.mirrors.current, {"__ddg1_": "z"}, _CHROMIUM_UA)
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    fake.get = MagicMock(return_value=_httpx_response(403, ""))
    with patch.object(httpx, "Client", return_value=fake):
        out = s._try_cookie_replay(f"{s.mirrors.current}/slow_download/abc/0/0")
    assert out is None
    assert s._cookie_cache.get(s.mirrors.current) is None


def test_replay_returns_none_when_page_has_no_partner_url(tmp_path: Path):
    """A clean 200 with no recognisable partner anchor is treated as a
    soft-fail: caller falls back to patchright. We do NOT invalidate
    cookies here -- they're still valid, this particular page just
    doesn't have what we want."""
    s = _make_scraper(tmp_path)
    s._cookie_cache.set(s.mirrors.current, {"__ddg1_": "z"}, _CHROMIUM_UA)
    html = "<html><head><title>fine</title></head><body>nothing useful</body></html>"
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    fake.get = MagicMock(return_value=_httpx_response(200, html))
    with patch.object(httpx, "Client", return_value=fake):
        out = s._try_cookie_replay(f"{s.mirrors.current}/slow_download/abc/0/0")
    assert out is None
    # Cookie still valid -- only soft-fail
    assert s._cookie_cache.get(s.mirrors.current) is not None


def test_replay_invalidates_on_network_error(tmp_path: Path):
    s = _make_scraper(tmp_path)
    s._cookie_cache.set(s.mirrors.current, {"__ddg1_": "z"}, _CHROMIUM_UA)
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=fake)
    fake.__exit__ = MagicMock(return_value=False)
    fake.get = MagicMock(side_effect=httpx.ConnectError("boom"))
    with patch.object(httpx, "Client", return_value=fake):
        out = s._try_cookie_replay(f"{s.mirrors.current}/slow_download/abc/0/0")
    assert out is None
    assert s._cookie_cache.get(s.mirrors.current) is None


def test_resolve_cdn_returns_none_for_empty_md5(tmp_path: Path):
    """Smoke: resolve_cdn must short-circuit when candidate has no md5,
    independent of the cookie path."""
    import dataclasses

    s = _make_scraper(tmp_path)
    cand_no_md5 = dataclasses.replace(_md5_candidate("x"), md5=None)
    assert s.resolve_cdn(cand_no_md5) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
