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
