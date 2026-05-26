"""Regression test: OpenSlumMonitor must fetch on the first get() call
even when time.monotonic() returns a small value at construction time.

Original bug: __init__ set _last_refresh = 0.0. The refresh trigger is
`now - _last_refresh >= poll_interval`. On a freshly-booted host (CI
runner, container at start), time.monotonic() can be < poll_interval,
so `now - 0 < poll_interval` and the first get() silently SKIPS the
fetch. Production callers then see {} forever until time advances
past poll_interval seconds.

Discovered when CI tests started failing intermittently with
'expected 1 fetch, got 0'. Previous commit fixed init to
float('-inf') so the first call always satisfies the threshold
regardless of clock value.
"""

from __future__ import annotations

from unittest.mock import patch

from endless_library.scrapers.open_slum import OpenSlumMonitor


def test_first_get_fetches_even_when_monotonic_is_smaller_than_poll_interval():
    """If `time.monotonic()` returns N where N < poll_interval at construction
    time, the first `get()` call MUST still trigger a fetch. The original
    bug was init using 0.0 which broke this on fresh systems."""
    fetch_count = [0]

    class _Counter(OpenSlumMonitor):
        def _fetch_remote(self):
            fetch_count[0] += 1
            return {"annas_archive": {"up": True}}

    # Simulate fresh CI runner: monotonic() = 100s, well below the 3600s
    # poll interval. Pre-fix behavior: 100 - 0 < 3600 -> no fetch.
    with patch("time.monotonic", return_value=100.0):
        m = _Counter(poll_interval=3600)
        m.get("annas_archive")
        m.get("annas_archive")  # cached

    assert fetch_count[0] == 1, (
        f"expected first get() to fetch when monotonic < poll_interval; "
        f"got fetch_count={fetch_count[0]}. This is the 'broke on fresh "
        f"host' regression from 2026-05."
    )


def test_first_get_fetches_when_monotonic_is_zero():
    """Even at monotonic()=0 (theoretical worst case), first get must fetch.
    Belt-and-suspenders pin to the same regression."""
    fetch_count = [0]

    class _Counter(OpenSlumMonitor):
        def _fetch_remote(self):
            fetch_count[0] += 1
            return {"annas_archive": {"up": True}}

    with patch("time.monotonic", return_value=0.0):
        m = _Counter(poll_interval=3600)
        m.get("annas_archive")

    assert fetch_count[0] == 1
