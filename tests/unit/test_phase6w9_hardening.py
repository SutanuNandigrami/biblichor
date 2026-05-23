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
