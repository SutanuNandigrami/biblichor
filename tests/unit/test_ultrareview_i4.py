"""Tests for ultrareview I4 (OpenSlumMonitor._refresh always updates _last_refresh)."""
from __future__ import annotations

import time


def test_open_slum_refresh_updates_timestamp_on_non_dict_response():
    """I4: _last_refresh must be updated even when _fetch_remote returns non-dict.
    Without the finally clause, a list/string response leaves _last_refresh=0,
    causing every call to get() to trigger another refresh (hammering the endpoint).
    """
    from endless_library.scrapers.open_slum import OpenSlumMonitor

    fetch_count = 0

    class _ListResponseMonitor(OpenSlumMonitor):
        def _fetch_remote(self):
            nonlocal fetch_count
            fetch_count += 1
            return ["not", "a", "dict"]  # Wrong type — simulates malformed API

    m = _ListResponseMonitor(poll_interval=3600)
    # First call: triggers refresh (fetch_count → 1), timestamp gets updated
    m.get("annas_archive")
    assert fetch_count == 1
    # Second call must NOT trigger another refresh (poll_interval=3600, timestamp was set)
    m.get("annas_archive")
    assert fetch_count == 1, (
        "I4 bug: _last_refresh not updated on non-dict response; "
        "every get() call hammers the endpoint"
    )


def test_open_slum_refresh_updates_timestamp_on_exception():
    """I4 pre-condition: _last_refresh is also updated when _fetch_remote raises.
    This was already true before I4; verify it stays true after the refactor.
    """
    from endless_library.scrapers.open_slum import OpenSlumMonitor

    fetch_count = 0

    class _ErrorMonitor(OpenSlumMonitor):
        def _fetch_remote(self):
            nonlocal fetch_count
            fetch_count += 1
            raise ConnectionError("simulated")

    m = _ErrorMonitor(poll_interval=3600)
    m.get("annas_archive")
    assert fetch_count == 1
    m.get("annas_archive")
    assert fetch_count == 1, "timestamp must be updated even when fetch raises"


def test_open_slum_cache_preserved_on_non_dict_response():
    """I4: when _fetch_remote returns a non-dict, the previous cache is preserved."""
    from endless_library.scrapers.open_slum import OpenSlumMonitor

    call_count = 0

    class _PatchedMonitor(OpenSlumMonitor):
        def _fetch_remote(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"annas_archive": {"up": True}}
            return "not a dict"

    # Force first refresh to populate cache (poll_interval=0 → always stale)
    m = _PatchedMonitor(poll_interval=0)
    result1 = m.get("annas_archive")
    assert result1 == {"up": True}

    # Second refresh returns non-dict; original cache must survive
    result2 = m.get("annas_archive")
    assert result2 == {"up": True}, "cache should be preserved on bad refresh response"
