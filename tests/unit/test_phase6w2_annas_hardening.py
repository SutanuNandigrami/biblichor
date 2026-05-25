# tests/unit/test_phase6w2_annas_hardening.py
import time


def test_next_mirror_returns_first_when_no_history():
    from endless_library.scrapers.annas_domains import next_mirror, _reset_state_for_tests
    _reset_state_for_tests()
    m = next_mirror()
    assert m in {"annas-archive.gl", "annas-archive.li", "annas-archive.pm", "annas-archive.pk", "annas-archive.gd"}


def test_mark_cool_skips_mirror_for_5min():
    from endless_library.scrapers.annas_domains import (
        next_mirror, mark_cool, _reset_state_for_tests, _MIRRORS,
    )
    _reset_state_for_tests()
    cooled = _MIRRORS[0]
    mark_cool(cooled)
    seen = set()
    for _ in range(10):
        seen.add(next_mirror())
    assert cooled not in seen


def test_mark_success_prefers_last_working():
    from endless_library.scrapers.annas_domains import (
        next_mirror, mark_success, _reset_state_for_tests, _MIRRORS,
    )
    _reset_state_for_tests()
    pick = _MIRRORS[2]
    mark_success(pick)
    assert next_mirror(prefer_last_working=True) == pick


def test_cool_expires_after_300_seconds(monkeypatch):
    import endless_library.scrapers.annas_domains as ad
    ad._reset_state_for_tests()
    ad.mark_cool(ad._MIRRORS[0])
    monkeypatch.setattr(ad, "_now", lambda: time.time() + 301)
    seen = set()
    for _ in range(10):
        seen.add(ad.next_mirror())
    assert ad._MIRRORS[0] in seen


def test_cf_bypass_client_gets_url_and_returns_html(monkeypatch):
    from endless_library.scrapers import cf_bypass_client
    posted = {}
    class _R:
        status_code = 200
        text = "<html>resolved</html>"
        def raise_for_status(self): pass
    def _fake_get(url, params=None, timeout=None, **kw):
        posted["url"] = url; posted["params"] = params
        return _R()
    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.httpx.get", _fake_get)
    monkeypatch.setenv("CF_BYPASS_URL", "http://test-bypass:8000")
    html = cf_bypass_client.resolve("https://annas-archive.gl/md5/abc")
    assert "<html>resolved</html>" in html
    assert posted["url"] == "http://test-bypass:8000/html"
    assert posted["params"] == {"url": "https://annas-archive.gl/md5/abc"}


def test_cf_bypass_client_raises_on_5xx(monkeypatch):
    import httpx
    from endless_library.scrapers import cf_bypass_client
    class _R:
        status_code = 502
        def raise_for_status(self): raise httpx.HTTPStatusError("502", request=None, response=None)
    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.httpx.get",
                        lambda *a, **kw: _R())
    monkeypatch.setenv("CF_BYPASS_URL", "http://test-bypass:8000")
    try:
        cf_bypass_client.resolve("https://x")
        assert False, "should have raised"
    except httpx.HTTPStatusError:
        pass


def test_annas_cloakbrowser_routes_through_sidecar(monkeypatch):
    from endless_library.scrapers.annas_cloakbrowser import AnnasArchiveCloakBrowser
    from endless_library.domain.models import SearchQuery
    seen_url = []
    def _fake_resolve(url, **kw):
        seen_url.append(url)
        return '<html><body><a href="/md5/abc">Sapiens</a></body></html>'
    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.resolve", _fake_resolve)
    cl = AnnasArchiveCloakBrowser(cfg=None)
    cands = cl.search(SearchQuery(title="Sapiens", author="Harari", isbn13="",
                                  format_priority=("epub",), language="en"))
    assert any("annas-archive" in u for u in seen_url)
    assert any(c.title == "Sapiens" for c in cands)


def test_annas_cloakbrowser_cools_mirror_on_resolve_failure(monkeypatch):
    from endless_library.scrapers.annas_cloakbrowser import AnnasArchiveCloakBrowser
    from endless_library.scrapers import annas_domains
    from endless_library.domain.models import SearchQuery
    annas_domains._reset_state_for_tests()
    def _fail(url, **kw):
        raise RuntimeError("sidecar down")
    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.resolve", _fail)
    cl = AnnasArchiveCloakBrowser(cfg=None)
    out = cl.search(SearchQuery(title="x", author="", isbn13="",
                                format_priority=("epub",), language="en"))
    assert out == []
    # at least one mirror should now be cool
    assert any(annas_domains._is_cool(m) for m in annas_domains._MIRRORS)

def test_cf_bypass_refuses_internal_url():
    from endless_library.scrapers.cf_bypass_client import resolve
    import pytest
    for bad in ("http://bookorbit:3000/", "http://flaresolverr:8191/",
                "http://127.0.0.1:8090/", "http://localhost/",
                "file:///etc/passwd", "ftp://example.com/"):
        with pytest.raises(ValueError, match="refusing to proxy"):
            resolve(bad)


def test_probe_slow_servers_safe_under_event_loop():
    """_run_async must not raise RuntimeError when called from within an
    already-running event loop (ultrareview I6)."""
    import asyncio
    from endless_library.scrapers.annas_curl import _run_async

    async def _inner():
        # A trivial coroutine that returns a value.
        async def _coro():
            return "ok"
        # Call _run_async from within a running event loop.
        return _run_async(_coro())

    result = asyncio.run(_inner())
    assert result == "ok", f"Expected 'ok', got {result!r}"
