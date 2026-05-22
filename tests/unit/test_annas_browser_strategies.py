"""annas_playwright + annas_cloakbrowser: ensure they're registered and
inherit parsing from AnnasArchiveCurl correctly."""

from __future__ import annotations

from endless_library.config import ScrapersCfg
from endless_library.scrapers import registry


def test_annas_playwright_registered():
    assert "annas_playwright" in registry.available()


def test_annas_cloakbrowser_registered():
    assert "annas_cloakbrowser" in registry.available()


def test_camoufox_stub_removed():
    assert "annas_camoufox" not in registry.available()


def test_playwright_inherits_parser():
    """search parser is inherited, so we can test it with fake _get."""
    from endless_library.domain.models import SearchQuery
    from endless_library.scrapers.annas_playwright import AnnasArchivePlaywright

    cfg = ScrapersCfg(
        annas_mirrors=["https://annas-archive.gl"],
        format_priority=["epub"],
        language="en",
        request_delay_seconds=0,
    )
    s = AnnasArchivePlaywright(cfg)
    # Inject fixture HTML
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "annas" / "search_pragmatic.html"
    s._get = lambda url: fixture.read_text()  # type: ignore[method-assign]
    out = s.search(
        SearchQuery(
            title="Pragmatic",
            author=None,
            isbn13=None,
            format_priority=("epub",),
            language="en",
        )
    )
    assert out and out[0].md5 == "a" * 32


def test_cloakbrowser_uses_sidecar(monkeypatch):
    """Phase 6w.2: cloakbrowser now routes through cf-bypass sidecar.
    Verify it calls cf_bypass_client.resolve and returns Candidates."""
    from endless_library.domain.models import SearchQuery
    from endless_library.scrapers import cf_bypass_client
    from endless_library.scrapers.annas_cloakbrowser import AnnasArchiveCloakBrowser

    called = []

    def _fake_resolve(url, **kw):
        called.append(url)
        return '<html><body><a href="/md5/abcdef1234567890abcdef1234567890">Test Book</a></body></html>'

    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.resolve", _fake_resolve)
    s = AnnasArchiveCloakBrowser(cfg=None)
    out = s.search(
        SearchQuery(
            title="Pragmatic",
            author=None,
            isbn13=None,
            format_priority=("epub",),
            language="en",
        )
    )
    assert called, "cf_bypass_client.resolve was not called"
    assert out and out[0].title == "Test Book"
