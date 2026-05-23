"""Shared async-to-sync bridge for biblichor.

Provides _run_async — a safe wrapper that runs a coroutine from sync code
whether or not an event loop is already running.

asyncio.run() raises RuntimeError when called from inside a running loop
(e.g. when called from a sync function that was itself called from async
code via run_in_executor). This wrapper falls back to a fresh
ThreadPoolExecutor-spawned event loop in that case (I-NEW-3).

Usage:
    from endless_library.async_utils import _run_async
    result = _run_async(some_async_fn())
"""
from __future__ import annotations

import asyncio
import concurrent.futures as _cf
import threading


_RUN_ASYNC_EXECUTOR: _cf.ThreadPoolExecutor | None = None
_RUN_ASYNC_LOCK = threading.Lock()


def _get_executor() -> _cf.ThreadPoolExecutor:
    """Return the shared module-level executor, creating it on first call (M-3rd-6)."""
    global _RUN_ASYNC_EXECUTOR
    with _RUN_ASYNC_LOCK:
        if _RUN_ASYNC_EXECUTOR is None:
            _RUN_ASYNC_EXECUTOR = _cf.ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="run-async"
            )
        return _RUN_ASYNC_EXECUTOR


def _run_async(coro):
    """Run an async coroutine from sync code, safe whether or not an event
    loop is already running.

    asyncio.run raises RuntimeError when called from inside a running loop;
    this wrapper falls back to a module-level cached ThreadPoolExecutor
    (max_workers=2) so two concurrent callers do not serialize (M-3rd-6).
    """
    try:
        asyncio.get_running_loop()
        # Already inside an event loop — run in a worker thread.
        return _get_executor().submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)
