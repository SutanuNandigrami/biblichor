"""Tests for the script-aware strategy ordering."""

from __future__ import annotations

from endless_library.config import ScrapersCfg
from endless_library.scrapers.registry import (
    enabled_order,
    enabled_order_for_query,
)


def _cfg() -> ScrapersCfg:
    return ScrapersCfg(
        order=[
            "annas_curl",
            "annas_flaresolverr",
            "libgen_curl",
            "archive_curl",
            "kindlebangla_curl",
        ],
        enabled={
            "annas_curl": True,
            "annas_flaresolverr": True,
            "libgen_curl": True,
            "archive_curl": True,
            "kindlebangla_curl": True,
        },
    )


def test_latin_query_keeps_configured_order():
    cfg = _cfg()
    out = enabled_order_for_query(cfg, "The Hate U Give")
    assert out == enabled_order(cfg)
    assert out[0] == "annas_curl"


def test_bengali_query_promotes_kindlebangla_to_front():
    cfg = _cfg()
    out = enabled_order_for_query(cfg, "কাঁটায়-কাঁটায় ৬")
    assert out[0] == "kindlebangla_curl"
    # The rest stay in their configured relative order
    assert out[1:] == [
        "annas_curl",
        "annas_flaresolverr",
        "libgen_curl",
        "archive_curl",
    ]


def test_devanagari_query_also_promotes_kindlebangla():
    cfg = _cfg()
    out = enabled_order_for_query(cfg, "रामायण")
    assert out[0] == "kindlebangla_curl"


def test_cjk_query_promotes_kindlebangla():
    cfg = _cfg()
    out = enabled_order_for_query(cfg, "我的奮鬥")
    # Bengali catalog won't have it, but priority list still wins; archive.org
    # and Anna's then fall back as configured.
    assert out[0] == "kindlebangla_curl"


def test_disabled_kindlebangla_is_not_promoted():
    cfg = _cfg()
    cfg.enabled["kindlebangla_curl"] = False
    out = enabled_order_for_query(cfg, "কাঁটায়-কাঁটায় ৬")
    assert "kindlebangla_curl" not in out
    # Order falls back to remaining enabled in configured order
    assert out == [
        "annas_curl",
        "annas_flaresolverr",
        "libgen_curl",
        "archive_curl",
    ]


def test_empty_title_falls_back_to_normal_order():
    cfg = _cfg()
    assert enabled_order_for_query(cfg, "") == enabled_order(cfg)


def test_accented_latin_not_treated_as_non_latin():
    """Café, Süß, naïve, etc. should NOT trigger Bengali promotion."""
    cfg = _cfg()
    out = enabled_order_for_query(cfg, "Café Brasileiro")
    assert out[0] == "annas_curl"
