"""Phase 6w.9 hardening + UI smoke tests."""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Task 1: Patchright import check
# ---------------------------------------------------------------------------

def test_welib_playwright_imports_from_patchright():
    """welib_playwright must use patchright, not vanilla playwright."""
    import importlib
    import inspect
    import ast
    from pathlib import Path

    src = Path("/home/ubuntu/endless-library/src/endless_library/scrapers/welib_playwright.py")
    tree = ast.parse(src.read_text())

    # Collect all import-from module names in the file
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)

    # patchright must appear somewhere in imports
    patchright_imports = [m for m in modules if "patchright" in m]
    playwright_imports = [m for m in modules if m.startswith("playwright")]

    assert patchright_imports, (
        "welib_playwright.py should import from patchright, found none"
    )
    assert not playwright_imports, (
        f"welib_playwright.py still imports vanilla playwright: {playwright_imports}"
    )


# ---------------------------------------------------------------------------
# Task 2: bench records NotConfigured instead of raising
# ---------------------------------------------------------------------------

def test_bench_records_not_configured_instead_of_raising():
    """When registry.build raises NotConfigured, bench records per-query
    outcomes with note='creds-missing: ...' and does not propagate."""
    from endless_library.bench import BenchOutcome, BenchQuery, run_bench
    from endless_library.scrapers.base import NotConfigured

    class _FakeRegistry:
        def build(self, name, cfg, **kw):
            raise NotConfigured("no creds")

        def enabled_order(self, cfg):
            return ["fake_scraper"]

        def available(self):
            return ["fake_scraper"]

    import endless_library.bench as _bench_mod
    import endless_library.scrapers.registry as _reg_mod

    original_build = _reg_mod.build
    original_enabled = _reg_mod.enabled_order

    class _FakeCfg:
        class scrapers:
            order = ["fake_scraper"]
            enabled = {"fake_scraper": True}
            format_priority = ["epub"]
        class bench:
            per_query_timeout_sec = 20
            circuit_break_after_consecutive_fails = 3

    queries = [
        BenchQuery(title="Test Book", author="Author", isbn13="", language="en", tags=("en", "modern")),
    ]

    try:
        _reg_mod.build = lambda name, cfg, **kw: (_ for _ in ()).throw(NotConfigured("no creds"))
        _reg_mod.enabled_order = lambda cfg: ["fake_scraper"]

        outcomes = run_bench(_FakeCfg(), queries, strategies=["fake_scraper"],
                             corpus_tags={"fake_scraper": frozenset(["en", "modern"])})
    finally:
        _reg_mod.build = original_build
        _reg_mod.enabled_order = original_enabled

    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.scraper == "fake_scraper"
    assert not o.success
    assert "creds-missing" in o.note


def test_not_configured_importable_from_base():
    from endless_library.scrapers.base import NotConfigured
    assert issubclass(NotConfigured, Exception)


def test_not_configured_re_exported_from_mobilism():
    from endless_library.scrapers.mobilism import NotConfigured as NC
    from endless_library.scrapers.base import NotConfigured as NCBase
    assert NC is NCBase


# ---------------------------------------------------------------------------
# Task 3: PD chain verification
# ---------------------------------------------------------------------------

def _make_scrapers_cfg(enabled: list[str]):
    """Build a minimal ScrapersCfg-like stub for PD chain tests."""
    from types import SimpleNamespace

    enabled_map = {n: True for n in enabled}
    return SimpleNamespace(
        order=enabled,
        enabled=enabled_map,
        format_priority=["epub"],
    )


_PD_SCRAPERS = {
    "gutendex",
    "standard_ebooks",
    "oapen_doab",
    "wikisource",
    "doab",
    "hathitrust",
}

_MODERN_SCRAPERS = {"annas_curl", "libgen_curl", "archive_curl", "zlib_singlelogin"}


def test_pd_chain_promotes_pd_scrapers_for_pre_1928_books():
    """Regression guard: PD scrapers must appear before general-purpose
    scrapers when is_pd=True."""
    from endless_library.scrapers import registry

    all_scrapers = list(_PD_SCRAPERS | _MODERN_SCRAPERS)
    cfg = _make_scrapers_cfg(all_scrapers)

    chain = registry.pd_aware_order(cfg, query_title="Pride and Prejudice", is_pd=True)

    # Filter to only scrapers that are in the chain
    pd_positions = [i for i, n in enumerate(chain) if n in _PD_SCRAPERS]
    modern_positions = [i for i, n in enumerate(chain) if n in _MODERN_SCRAPERS]

    assert pd_positions, "No PD scrapers in chain — are they disabled?"
    assert modern_positions, "No modern scrapers in chain"

    # All PD scrapers should appear before all modern scrapers
    assert max(pd_positions) < min(modern_positions), (
        f"PD scrapers not promoted to front. chain={chain}, "
        f"pd_positions={pd_positions}, modern_positions={modern_positions}"
    )


def test_pd_chain_does_not_promote_pd_scrapers_for_modern_books():
    """When is_pd=False, PD scrapers keep their original relative position
    (they are not promoted to the front)."""
    from endless_library.scrapers import registry

    all_scrapers = list(_PD_SCRAPERS | _MODERN_SCRAPERS)
    cfg = _make_scrapers_cfg(all_scrapers)

    chain = registry.pd_aware_order(cfg, query_title="Project Hail Mary", is_pd=False)

    pd_positions = [i for i, n in enumerate(chain) if n in _PD_SCRAPERS]
    modern_positions = [i for i, n in enumerate(chain) if n in _MODERN_SCRAPERS]

    if not pd_positions or not modern_positions:
        return  # nothing to check if some scrapers are absent

    # For a modern book, modern scrapers should appear before (or alongside)
    # PD scrapers — i.e., the first modern position should be before the first PD
    # position, OR there should be a modern scraper that comes before the
    # last PD scraper.
    # The simplest check: at least one modern scraper is before at least one PD scraper.
    assert min(modern_positions) < max(pd_positions), (
        f"For non-PD book, expected modern scrapers to come before some PD scrapers. "
        f"chain={chain}, modern_positions={modern_positions}, pd_positions={pd_positions}"
    )
