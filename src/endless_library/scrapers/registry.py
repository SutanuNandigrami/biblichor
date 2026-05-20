from __future__ import annotations

from typing import Any

from endless_library.config import ScrapersCfg
from endless_library.scrapers.annas_cloakbrowser import AnnasArchiveCloakBrowser
from endless_library.scrapers.annas_curl import AnnasArchiveCurl
from endless_library.scrapers.annas_flaresolverr import AnnasArchiveFlareSolverr
from endless_library.scrapers.annas_playwright import AnnasArchivePlaywright
from endless_library.scrapers.archive_curl import ArchiveOrgCurl

# Phase 6s.1 — public-domain curated sources
from endless_library.scrapers.gutendex import Gutendex
from endless_library.scrapers.kindlebangla_curl import KindleBanglaCurl
from endless_library.scrapers.libgen_curl import LibgenCurl
from endless_library.scrapers.oapen_doab import OapenDoab
from endless_library.scrapers.standard_ebooks import StandardEbooks
from endless_library.scrapers.welib_curl import WelibCurl
from endless_library.scrapers.welib_playwright import WelibPlaywright
from endless_library.scrapers.wikisource import Wikisource
from endless_library.scrapers.zlib_singlelogin import ZlibSingleLogin

_REGISTRY = {
    "annas_curl": AnnasArchiveCurl,
    "annas_flaresolverr": AnnasArchiveFlareSolverr,
    "annas_playwright": AnnasArchivePlaywright,
    "annas_cloakbrowser": AnnasArchiveCloakBrowser,
    "welib_curl": WelibCurl,
    "welib_playwright": WelibPlaywright,
    "libgen_curl": LibgenCurl,
    "archive_curl": ArchiveOrgCurl,
    "kindlebangla_curl": KindleBanglaCurl,
    "gutendex": Gutendex,
    "standard_ebooks": StandardEbooks,
    "oapen_doab": OapenDoab,
    "wikisource": Wikisource,
    "zlib_singlelogin": ZlibSingleLogin,
}

_PD_PRIORITY = ("standard_ebooks", "gutendex", "wikisource", "oapen_doab")


def available() -> list[str]:
    return list(_REGISTRY.keys())


def build(name: str, cfg: ScrapersCfg, **kwargs: Any):
    if name not in _REGISTRY:
        raise KeyError(f"unknown scraper: {name}")
    return _REGISTRY[name](cfg, **kwargs)


def enabled_order(cfg: ScrapersCfg) -> list[str]:
    """Return strategy names from cfg.order that are also in cfg.enabled and True."""
    return [n for n in cfg.order if cfg.enabled.get(n, False) and n in _REGISTRY]


# Strategies that natively serve non-Latin (esp. Bengali) catalogs. When the
# query title is non-Latin, we promote these to the head of the iteration
# order so we don't waste a roundtrip through Anna's English-fallback noise.
_NON_LATIN_PRIORITY = ("kindlebangla_curl",)


def enabled_order_for_query(cfg: ScrapersCfg, query_title: str) -> list[str]:
    """Like enabled_order but reordered for the query's script.

    Non-Latin query title -> _NON_LATIN_PRIORITY scrapers come first (if
    they're also enabled), then the rest of the configured order with
    those scrapers removed (so they don't appear twice). Latin queries
    are unchanged.
    """
    from endless_library.domain.scoring import _is_non_latin

    base = enabled_order(cfg)
    if not query_title or not _is_non_latin(query_title):
        return base
    promoted = [n for n in _NON_LATIN_PRIORITY if n in base]
    rest = [n for n in base if n not in promoted]
    return promoted + rest


def pd_aware_order(cfg: ScrapersCfg, *, query_title: str, is_pd: bool) -> list[str]:
    """Like enabled_order_for_query but promotes the public-domain
    curated scrapers (Standard Ebooks, Gutendex, Wikisource,
    OAPEN/DOAB) to the front when is_pd=True. Caller computes is_pd
    from books.is_public_domain OR books.pub_year < 1928.

    Phase 6s.1.
    """
    base = enabled_order_for_query(cfg, query_title)
    if not is_pd:
        return base
    promoted = [n for n in _PD_PRIORITY if n in base]
    rest = [n for n in base if n not in promoted]
    return promoted + rest
