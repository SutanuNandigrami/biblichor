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


def _run_async(coro):
    """Run an async coroutine from sync code, safe whether or not an event
    loop is already running.

    asyncio.run raises RuntimeError when called from inside a running loop;
    this wrapper falls back to a fresh ThreadPoolExecutor-spawned event loop
    in that case (I-NEW-3).
    """
    try:
        asyncio.get_running_loop()
        # Already inside an event loop — spin up a fresh one in a worker thread.
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)
