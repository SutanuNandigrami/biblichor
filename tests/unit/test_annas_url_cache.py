"""Unit tests for PartnerURLCache + AnnasArchivePatchright url-cache hooks.

PR #40: cache resolved partner CDN URLs keyed by md5. Sub-ms hit on
repeat md5 within the TTL window; invalidate on partner 4xx.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from endless_library.config import Config
from endless_library.domain.models import Candidate
from endless_library.scrapers.annas_browser_pool import BrowserPool
from endless_library.scrapers.annas_ddg_cookies import DDGCookieCache
from endless_library.scrapers.annas_patchright import (
    _CHROMIUM_UA,
    AnnasArchivePatchright,
)
from endless_library.scrapers.annas_url_cache import (
    PartnerURLCache,
)

# ============ PartnerURLCache ============


_TEST_MD5 = "e0bf3aa4103c8c4c779dfae62b1dd52a"
_TEST_URL = "https://b4mcx2ml.net/d3/abc~xyz"


def test_set_then_get_returns_same_url(tmp_path: Path):
    c = PartnerURLCache(path=tmp_path / "u.json")
    c.set(_TEST_MD5, _TEST_URL)
    e = c.get(_TEST_MD5)
    assert e is not None
    assert e.url == _TEST_URL


def test_get_returns_none_when_expired(tmp_path: Path):
    c = PartnerURLCache(path=tmp_path / "u.json")
    c.set(_TEST_MD5, _TEST_URL, ttl_seconds=0)
    time.sleep(0.01)
    assert c.get(_TEST_MD5) is None


def test_invalidate_removes_entry(tmp_path: Path):
    c = PartnerURLCache(path=tmp_path / "u.json")
    c.set(_TEST_MD5, _TEST_URL)
    c.invalidate(_TEST_MD5)
    assert c.get(_TEST_MD5) is None


def test_persistence_across_instances(tmp_path: Path):
    p = tmp_path / "u.json"
    PartnerURLCache(path=p).set(_TEST_MD5, _TEST_URL)
    e = PartnerURLCache(path=p).get(_TEST_MD5)
    assert e is not None
    assert e.url == _TEST_URL


def test_no_path_means_in_memory_only(tmp_path: Path):
    c = PartnerURLCache(path=None)
    c.set(_TEST_MD5, _TEST_URL)
    assert c.get(_TEST_MD5) is not None


def test_corrupt_file_does_not_explode(tmp_path: Path):
    p = tmp_path / "u.json"
    p.write_text("not json {")
    c = PartnerURLCache(path=p)
    assert c.get(_TEST_MD5) is None
    c.set(_TEST_MD5, _TEST_URL)
    assert c.get(_TEST_MD5) is not None


def test_invalidate_persists_to_file(tmp_path: Path):
    p = tmp_path / "u.json"
    c1 = PartnerURLCache(path=p)
    c1.set(_TEST_MD5, _TEST_URL)
    c1.invalidate(_TEST_MD5)
    assert PartnerURLCache(path=p).get(_TEST_MD5) is None
    data = json.loads(p.read_text())
    assert _TEST_MD5 not in data


def test_set_overwrites_existing_entry(tmp_path: Path):
    c = PartnerURLCache(path=tmp_path / "u.json")
    c.set(_TEST_MD5, "https://old.example/d3/aaa")
    c.set(_TEST_MD5, "https://new.example/d3/bbb")
    e = c.get(_TEST_MD5)
    assert e is not None
    assert e.url == "https://new.example/d3/bbb"


def test_multiple_md5s_isolated(tmp_path: Path):
    c = PartnerURLCache(path=tmp_path / "u.json")
    c.set("a" * 32, "https://a.example/d3/")
    c.set("b" * 32, "https://b.example/d3/")
    c.invalidate("a" * 32)
    assert c.get("a" * 32) is None
    assert c.get("b" * 32) is not None


# ============ AnnasArchivePatchright url_cache integration ============


def _make_scraper(tmp_path: Path) -> AnnasArchivePatchright:
    cookie_cache = DDGCookieCache(path=tmp_path / "cookies.json")
    url_cache = PartnerURLCache(path=tmp_path / "urls.json")
    # Pass an injected pool with a no-op launcher so resolve_cdn doesn't
    # try to actually drive a browser if it reaches that path.
    pool = BrowserPool(launcher=lambda h: (MagicMock(), MagicMock()))
    return AnnasArchivePatchright(
        Config().scrapers,
        cookie_cache=cookie_cache,
        browser_pool=pool,
        url_cache=url_cache,
    )


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


def test_resolve_cdn_returns_cached_url_without_network(tmp_path: Path):
    """If md5 is in url_cache, resolve_cdn must not call httpx or the
    browser pool -- it just hands back the cached URL."""
    s = _make_scraper(tmp_path)
    s._url_cache.set(_TEST_MD5, _TEST_URL)
    fake_httpx = MagicMock()
    fake_httpx.Client = MagicMock(side_effect=AssertionError("must not be called"))
    with patch.object(httpx, "Client", fake_httpx.Client):
        handle = s.resolve_cdn(_md5_candidate(_TEST_MD5))
    assert handle is not None
    assert handle.url == _TEST_URL


def test_resolve_cdn_caches_url_after_cookie_replay_hit(tmp_path: Path):
    """A cookie-replay HIT populates the url cache so the next call
    with the same md5 is a sub-ms url-cache HIT."""
    s = _make_scraper(tmp_path)
    s._cookie_cache.set(s.mirrors.current, {"__ddg1_": "z"}, _CHROMIUM_UA)
    html = (
        "<html><head><title>Anna's Archive</title></head><body>"
        f'<a href="{_TEST_URL}">📚 Download now</a></body></html>'
    )
    fake_resp = MagicMock(status_code=200, text=html)
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.get = MagicMock(return_value=fake_resp)
    with patch.object(httpx, "Client", return_value=fake_client):
        h1 = s.resolve_cdn(_md5_candidate(_TEST_MD5))
    assert h1 is not None
    assert h1.url == _TEST_URL
    # url_cache should now have it
    cached = s._url_cache.get(_TEST_MD5)
    assert cached is not None
    assert cached.url == _TEST_URL


def test_invalidate_md5_evicts_from_url_cache(tmp_path: Path):
    s = _make_scraper(tmp_path)
    s._url_cache.set(_TEST_MD5, _TEST_URL)
    s.invalidate_md5(_TEST_MD5)
    assert s._url_cache.get(_TEST_MD5) is None


def test_resolve_cdn_short_circuits_on_empty_md5(tmp_path: Path):
    s = _make_scraper(tmp_path)
    cand_no_md5 = dataclasses.replace(_md5_candidate("x"), md5=None)
    assert s.resolve_cdn(cand_no_md5) is None


def test_url_cache_hit_takes_precedence_over_cookie_replay(tmp_path: Path):
    """Even with cached DDG cookies and a happy mocked httpx, url cache
    hit must short-circuit BEFORE the cookie-replay fires."""
    s = _make_scraper(tmp_path)
    s._url_cache.set(_TEST_MD5, _TEST_URL)
    s._cookie_cache.set(s.mirrors.current, {"__ddg1_": "z"}, _CHROMIUM_UA)
    # If we reach httpx, this raises.
    with patch.object(
        httpx,
        "Client",
        side_effect=AssertionError("cookie replay must not fire when url cache hits"),
    ):
        h = s.resolve_cdn(_md5_candidate(_TEST_MD5))
    assert h is not None
    assert h.url == _TEST_URL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
