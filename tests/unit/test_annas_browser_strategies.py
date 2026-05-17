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


def test_cloakbrowser_inherits_parser():
    from endless_library.domain.models import SearchQuery
    from endless_library.scrapers.annas_cloakbrowser import AnnasArchiveCloakBrowser

    cfg = ScrapersCfg(
        annas_mirrors=["https://annas-archive.gl"],
        format_priority=["epub"],
        language="en",
        request_delay_seconds=0,
    )
    s = AnnasArchiveCloakBrowser(cfg)
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
