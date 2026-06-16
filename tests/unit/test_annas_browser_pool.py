"""Unit tests for BrowserPool.

PR #39: process-wide Chromium pool. Tests cover spawn/reuse/recycle
semantics without launching a real browser (the pool is constructed
with a mock launcher that returns sentinel objects).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from endless_library.scrapers.annas_browser_pool import BrowserPool


def _make_fake_launcher() -> MagicMock:
    """Returns a launcher callable + tracks spawned (pw, browser) pairs."""
    launcher = MagicMock()
    launcher.spawn_count = 0

    def _fn(headless: bool) -> tuple[MagicMock, MagicMock]:
        launcher.spawn_count += 1
        pw = MagicMock(name=f"pw_{launcher.spawn_count}")
        browser = MagicMock(name=f"browser_{launcher.spawn_count}")
        launcher.last_pw = pw
        launcher.last_browser = browser
        return pw, browser

    launcher.side_effect = _fn
    launcher.fn = _fn
    return launcher


def test_first_acquire_spawns_browser():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn)
    b = p.acquire()
    assert fl.spawn_count == 1
    assert b is fl.last_browser
    assert p.is_alive
    assert p.uses == 1


def test_second_acquire_reuses_same_browser():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn)
    b1 = p.acquire()
    b2 = p.acquire()
    assert fl.spawn_count == 1
    assert b1 is b2
    assert p.uses == 2


def test_recycle_after_max_uses():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn, max_uses=2)
    b1 = p.acquire()
    b2 = p.acquire()
    b3 = p.acquire()  # third triggers recycle
    assert fl.spawn_count == 2
    assert b1 is b2
    assert b3 is not b1
    assert p.uses == 1


def test_recycle_closes_old_browser():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn, max_uses=1)
    b1 = p.acquire()
    p.acquire()  # forces recycle
    b1.close.assert_called_once()


def test_report_failure_forces_respawn():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn)
    b1 = p.acquire()
    p.report_failure()
    assert not p.is_alive
    b2 = p.acquire()
    assert fl.spawn_count == 2
    assert b2 is not b1


def test_report_failure_closes_browser():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn)
    b = p.acquire()
    p.report_failure()
    b.close.assert_called_once()


def test_shutdown_closes_browser_and_stops_pw():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn)
    p.acquire()
    pw = fl.last_pw
    browser = fl.last_browser
    p.shutdown()
    browser.close.assert_called_once()
    pw.stop.assert_called_once()
    assert not p.is_alive


def test_close_failure_during_recycle_does_not_propagate():
    """If browser.close() throws (already dead), recycle still proceeds."""
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn, max_uses=1)
    b1 = p.acquire()
    b1.close.side_effect = RuntimeError("already dead")
    b2 = p.acquire()  # would raise if recycle didn't swallow the close error
    assert b2 is not b1
    assert fl.spawn_count == 2


def test_uses_counter_resets_after_recycle():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn, max_uses=3)
    for _ in range(3):
        p.acquire()
    assert p.uses == 3
    p.acquire()  # triggers recycle
    assert p.uses == 1


def test_is_alive_reflects_state():
    fl = _make_fake_launcher()
    p = BrowserPool(launcher=fl.fn)
    assert not p.is_alive
    p.acquire()
    assert p.is_alive
    p.shutdown()
    assert not p.is_alive


def test_launcher_receives_headless_arg():
    received = []

    def launcher(headless: bool):
        received.append(headless)
        return MagicMock(), MagicMock()

    p = BrowserPool(launcher=launcher, headless=False)
    p.acquire()
    assert received == [False]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
