"""Phase 6m.iii — tests for M-6 startup nudge + M-7 image pin."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from endless_library.app import _probe_bookorbit_health

# ============ M-6 startup nudge ============


def test_probe_returns_true_on_200():
    """Happy path: live BookOrbit returns 200 -> probe says reachable."""
    def fake_get(url, timeout):
        return httpx.Response(200, request=httpx.Request("GET", url))
    with patch("httpx.get", fake_get):
        assert _probe_bookorbit_health("http://x:3000") is True


def test_probe_returns_false_on_non_200():
    """Anything other than 200 -> not reachable."""
    def fake_get(url, timeout):
        return httpx.Response(500, request=httpx.Request("GET", url))
    with patch("httpx.get", fake_get):
        assert _probe_bookorbit_health("http://x:3000") is False


def test_probe_returns_false_on_connection_error():
    """Connection refused / DNS failure -> not reachable."""
    def fake_get(url, timeout):
        raise httpx.ConnectError("simulated refused")
    with patch("httpx.get", fake_get):
        assert _probe_bookorbit_health("http://x:3000") is False


def test_probe_returns_false_on_timeout():
    """Timeout -> not reachable. Probe must NOT raise."""
    def fake_get(url, timeout):
        raise httpx.TimeoutException("simulated")
    with patch("httpx.get", fake_get):
        assert _probe_bookorbit_health("http://x:3000") is False


def test_probe_returns_false_on_empty_url():
    """Empty url short-circuits without an HTTP call."""
    assert _probe_bookorbit_health("") is False


def test_probe_hits_health_path():
    """Confirms /api/v1/health is the path we probe — matches BookOrbit's
    actual endpoint."""
    seen = []
    def fake_get(url, timeout):
        seen.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))
    with patch("httpx.get", fake_get):
        _probe_bookorbit_health("http://x:3000/")
    assert seen == ["http://x:3000/api/v1/health"]


def test_probe_normalizes_trailing_slash():
    """Both `http://x:3000` and `http://x:3000/` produce the same probe URL."""
    seen = []
    def fake_get(url, timeout):
        seen.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url))
    with patch("httpx.get", fake_get):
        _probe_bookorbit_health("http://x:3000")
        _probe_bookorbit_health("http://x:3000/")
    assert seen[0] == seen[1] == "http://x:3000/api/v1/health"


# ============ pipeline import hoist (Improvement #5) ============


def test_drop_into_library_imported_at_pipeline_module_top():
    """The lazy `from endless_library.bookorbit.drop import ...` inside
    _process_from_downloaded was hoisted to the module top. Tests via
    inspect that the symbol is now an attribute of the pipeline module."""
    import endless_library.pipeline as pipeline_mod
    assert hasattr(pipeline_mod, "drop_into_library")
    assert hasattr(pipeline_mod, "BookOrbitDropError")


def test_pipeline_source_does_not_use_lazy_bookorbit_import():
    """Pins Improvement #5: the inline import must NOT come back. If a
    future refactor reintroduces it, this test fails."""
    import inspect

    import endless_library.pipeline as pipeline_mod
    src = inspect.getsource(pipeline_mod)
    # The only place "from endless_library.bookorbit" appears should be
    # at the module top — count and verify location.
    lines = src.splitlines()
    bookorbit_imports = [
        (i, ln) for i, ln in enumerate(lines)
        if "from endless_library.bookorbit" in ln and not ln.lstrip().startswith("#")
    ]
    assert len(bookorbit_imports) == 1, f"expected 1 import, got {bookorbit_imports}"
    # And it's near the top (within first 40 lines of the file = module imports area)
    line_no, _ = bookorbit_imports[0]
    assert line_no < 40, f"bookorbit import lives at line {line_no}, not at module top"
