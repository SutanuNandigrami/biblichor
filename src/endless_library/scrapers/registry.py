from __future__ import annotations

from typing import Any

from endless_library.config import ScrapersCfg
from endless_library.scrapers.annas_camoufox import AnnasArchiveCamoufox
from endless_library.scrapers.annas_curl import AnnasArchiveCurl
from endless_library.scrapers.annas_flaresolverr import AnnasArchiveFlareSolverr
from endless_library.scrapers.annas_playwright import AnnasArchivePlaywright
from endless_library.scrapers.libgen_curl import LibgenCurl
from endless_library.scrapers.welib_curl import WelibCurl
from endless_library.scrapers.welib_playwright import WelibPlaywright

_REGISTRY = {
    "annas_curl": AnnasArchiveCurl,
    "annas_flaresolverr": AnnasArchiveFlareSolverr,
    "annas_playwright": AnnasArchivePlaywright,
    "annas_camoufox": AnnasArchiveCamoufox,
    "welib_curl": WelibCurl,
    "welib_playwright": WelibPlaywright,
    "libgen_curl": LibgenCurl,
}


def available() -> list[str]:
    return list(_REGISTRY.keys())


def build(name: str, cfg: ScrapersCfg, **kwargs: Any):
    if name not in _REGISTRY:
        raise KeyError(f"unknown scraper: {name}")
    return _REGISTRY[name](cfg, **kwargs)


def enabled_order(cfg: ScrapersCfg) -> list[str]:
    """Return strategy names from cfg.order that are also in cfg.enabled and True."""
    return [n for n in cfg.order if cfg.enabled.get(n, False) and n in _REGISTRY]
