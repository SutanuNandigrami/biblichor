"""Tests for Phase 6z Fix 7 (I-NEW-3): kindle.py uses _run_async instead of
bare asyncio.run.

asyncio.run() raises RuntimeError when called from within a running event loop.
send_to_kindle must work when invoked from an async context (e.g. via executor).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _make_kindle_cfg(tmp_path):
    return SimpleNamespace(
        recipient="test@kindle.com",
        attachment_max_mb=50,
        subject="{title} by {author}",
    )


def _make_smtp_cfg():
    return SimpleNamespace(
        host="smtp.example.com",
        port=587,
        user="user@gmail.com",
        password="secret",
    )


def test_run_async_module_exists():
    """async_utils module must exist and export _run_async."""
    from endless_library.async_utils import _run_async
    assert callable(_run_async)


def test_run_async_works_outside_event_loop():
    """_run_async must work when no event loop is running."""
    from endless_library.async_utils import _run_async

    async def _coro():
        return 42

    result = _run_async(_coro())
    assert result == 42


def test_run_async_works_inside_running_event_loop():
    """_run_async must NOT raise RuntimeError when called from within asyncio.run."""
    from endless_library.async_utils import _run_async

    async def _inner():
        return 99

    async def _outer():
        # _run_async from inside a running loop — this is the I-NEW-3 hazard
        return _run_async(_inner())

    result = asyncio.run(_outer())
    assert result == 99


def test_kindle_send_safe_under_running_event_loop(tmp_path):
    """send_to_kindle must not crash when called from within asyncio.run."""
    from endless_library.kindle import send_to_kindle, SendResult

    # Create a small fake attachment
    attachment = tmp_path / "book.epub"
    attachment.write_bytes(b"fake epub content")

    kindle_cfg = _make_kindle_cfg(tmp_path)
    smtp_cfg = _make_smtp_cfg()

    fake_result = SendResult(accepted=True, response="250 OK test-123")

    async def _test():
        with patch("endless_library.kindle._send_smtp", new=AsyncMock(return_value=fake_result)):
            result = send_to_kindle(
                attachment=attachment,
                kindle=kindle_cfg,
                smtp=smtp_cfg,
                title="Test Book",
                author="Test Author",
            )
        return result

    result = asyncio.run(_test())
    assert result.accepted is True


def test_annas_curl_reexports_run_async():
    """annas_curl._run_async must still be importable (back-compat)."""
    from endless_library.scrapers.annas_curl import _run_async
    assert callable(_run_async)
