from __future__ import annotations

from endless_library.config import ScrapersCfg
from endless_library.scrapers import registry


def test_available_includes_all_six():
    names = registry.available()
    assert {
        "annas_curl",
        "annas_flaresolverr",
        "annas_playwright",
        "annas_camoufox",
        "welib_curl",
        "libgen_curl",
    } <= set(names)


def test_build_returns_correct_class():
    cfg = ScrapersCfg(annas_mirrors=["https://annas-archive.gl"])
    s = registry.build("annas_curl", cfg)
    assert s.name == "annas_curl"
    assert s.provider == "annas"


def test_enabled_order_filters_disabled():
    cfg = ScrapersCfg(
        order=["annas_curl", "welib_curl", "libgen_curl"],
        enabled={"annas_curl": True, "welib_curl": False, "libgen_curl": True},
    )
    assert registry.enabled_order(cfg) == ["annas_curl", "libgen_curl"]


def test_enabled_order_drops_unknown():
    cfg = ScrapersCfg(order=["nope", "annas_curl"], enabled={"annas_curl": True, "nope": True})
    assert registry.enabled_order(cfg) == ["annas_curl"]
