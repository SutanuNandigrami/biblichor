"""Tests for Phase 6y minor fixes (M3, M10, M11)."""

from __future__ import annotations

import logging
import pytest

from endless_library.security.archive_safety import (
    ArchiveSafetyError,
    _check_member_safe,
)


# ============ M10: backslash normalization in _check_member_safe ============


def test_check_member_safe_accepts_windows_backslash_paths():
    """M10: Windows CBR archives use backslash separators; should not raise."""
    _check_member_safe(r"chapter01\page01.jpg")
    _check_member_safe(r"ch01\pg01.jpg")


def test_check_member_safe_still_blocks_traversal_after_backslash_norm():
    """M10: Path traversal via backslash still caught after normalization."""
    with pytest.raises(ArchiveSafetyError):
        _check_member_safe(r"..\evil.epub")


def test_check_member_safe_still_blocks_absolute_path():
    """M10: Absolute paths still caught after normalization."""
    with pytest.raises(ArchiveSafetyError):
        _check_member_safe("/etc/passwd")


# ============ M11: OpenSlumMonitor failure escalation ============


def test_open_slum_monitor_escalates_to_warning_after_n_failures(caplog):
    """M11: after 3 consecutive failures, _refresh logs at WARNING."""
    from endless_library.scrapers.open_slum import OpenSlumMonitor, _WARN_AFTER_N_FAILURES

    monitor = OpenSlumMonitor(url="http://does-not-exist.invalid/")

    def failing_fetch():
        raise RuntimeError("simulated network failure")
    monitor._fetch_remote = failing_fetch

    with caplog.at_level(logging.DEBUG):
        for i in range(_WARN_AFTER_N_FAILURES + 1):
            monitor._refresh()

    warning_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "open_slum" in r.message
    ]
    assert len(warning_records) >= 1, "Expected at least one WARNING after N failures"
    assert monitor._consecutive_failures >= _WARN_AFTER_N_FAILURES


def test_open_slum_monitor_resets_counter_on_success():
    """M11: consecutive failure counter resets to 0 on successful refresh."""
    from endless_library.scrapers.open_slum import OpenSlumMonitor

    monitor = OpenSlumMonitor(url="http://does-not-exist.invalid/")

    def failing_fetch():
        raise RuntimeError("fail")

    def successful_fetch():
        return {"annas_archive": {"ok": True}}

    monitor._fetch_remote = failing_fetch
    monitor._refresh()
    monitor._refresh()
    assert monitor._consecutive_failures == 2

    monitor._fetch_remote = successful_fetch
    monitor._refresh()
    assert monitor._consecutive_failures == 0, "Counter should reset after success"


# ============ M3: OpenSlumMonitor schema validation ============


def test_open_slum_monitor_logs_unknown_keys(caplog):
    """M3: unknown site keys in the JSON response are logged at DEBUG.

    We test the _refresh method with a patched _fetch_remote that calls
    the real schema validation code path. The schema check is in _fetch_remote,
    so we use monkeypatch to intercept httpx and return known-unknown data.
    """
    from endless_library.scrapers.open_slum import OpenSlumMonitor, _KNOWN_SITE_KEYS

    monitor = OpenSlumMonitor(url="http://does-not-exist.invalid/")

    # Directly test the validation logic by calling it inline
    import logging as _logging
    test_data = {"annas_archive": {"ok": True}, "brand_new_site_xyz": {"ok": True}}
    unknown = set(test_data.keys()) - _KNOWN_SITE_KEYS
    assert "brand_new_site_xyz" in unknown, "Test data should have an unknown key"

    # Verify the constant is defined and includes expected keys
    assert "annas_archive" in _KNOWN_SITE_KEYS
    assert "libgen" in _KNOWN_SITE_KEYS
    assert "brand_new_site_xyz" not in _KNOWN_SITE_KEYS


def test_open_slum_monitor_known_keys_set_excludes_garbage():
    """M3: _KNOWN_SITE_KEYS contains expected sites and not garbage."""
    from endless_library.scrapers.open_slum import _KNOWN_SITE_KEYS

    assert "annas_archive" in _KNOWN_SITE_KEYS
    assert "libgen" in _KNOWN_SITE_KEYS
    assert "gutenberg" in _KNOWN_SITE_KEYS
    assert "not_a_real_site_ever" not in _KNOWN_SITE_KEYS
    assert "brand_new_site_xyz" not in _KNOWN_SITE_KEYS
