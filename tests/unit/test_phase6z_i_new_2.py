"""Tests for Phase 6z Fix 6 (I-NEW-2): Mobilism drift probe rate-limited.

The probe used to fire on every empty search result. Now it's rate-limited
to once per _DRIFT_PROBE_TTL seconds (6 hours).
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock


def _reset_drift_probe():
    """Reset the module-level probe timestamp between tests."""
    import endless_library.scrapers.mobilism_books as m

    m._drift_probe_last_at = 0.0


def test_mobilism_drift_probe_rate_limited():
    """Calling _check_drift twice in quick succession should only fire one GET."""
    import endless_library.scrapers.mobilism_books as mob

    _reset_drift_probe()

    call_count = [0]

    class _FakeSession:
        def get(self, url, **kw):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body>some results</body></html>"
            return resp

    session = _FakeSession()

    # First call — should fire
    mob._check_drift(session)
    assert call_count[0] == 1, "First probe call should fire a GET"

    # Second call immediately after — should be rate-limited
    mob._check_drift(session)
    assert call_count[0] == 1, "Second probe call should be skipped (rate-limited)"


def test_mobilism_drift_probe_fires_after_ttl(monkeypatch):
    """After TTL expires, the probe should fire again."""
    import endless_library.scrapers.mobilism_books as mob

    _reset_drift_probe()

    call_count = [0]

    class _FakeSession:
        def get(self, url, **kw):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html><body><a class='topictitle' href='/t1'>Book</a></body></html>"
            return resp

    session = _FakeSession()

    # First call
    mob._check_drift(session)
    assert call_count[0] == 1

    # Simulate TTL expiry by setting last-probe to old time
    mob._drift_probe_last_at = time.time() - mob._DRIFT_PROBE_TTL - 1

    # Should fire again
    mob._check_drift(session)
    assert call_count[0] == 2, "Probe should fire again after TTL expired"


def test_mobilism_drift_probe_not_called_on_first_call_rate_limited():
    """Module-level timestamp starts at 0 so first probe always fires."""
    import endless_library.scrapers.mobilism_books as mob

    _reset_drift_probe()
    assert mob._drift_probe_last_at == 0.0

    call_count = [0]

    class _FakeSession:
        def get(self, url, **kw):
            call_count[0] += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "<html></html>"
            return resp

    mob._check_drift(_FakeSession())
    assert call_count[0] == 1
    # Timestamp now set
    assert mob._drift_probe_last_at > 0
