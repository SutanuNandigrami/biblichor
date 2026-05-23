"""Open Slum upstream health monitor (Phase 6w.9d).

OpenSlumMonitor polls a remote status endpoint for known upstream sources.
Results are cached for a configurable poll interval to avoid hammering the
endpoint. The monitor swallows all errors internally — it is best-effort
health data only; a failure to refresh does not affect the main pipeline.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# Default URL for the Open Library / Anna's upstream status JSON
_DEFAULT_STATUS_URL = "https://status.annas-archive.se/status.json"

# How long a cached result stays valid (seconds)
_DEFAULT_POLL_INTERVAL = 300  # 5 minutes


class OpenSlumMonitor:
    """Poll-interval-guarded upstream status monitor.

    Usage::

        monitor = OpenSlumMonitor()
        status = monitor.get("annas_archive")  # None if unknown / not yet fetched

    Thread safety: each call to ``get`` that triggers a refresh does so
    synchronously.  For an async context wrap the blocking call in
    ``asyncio.to_thread``.
    """

    def __init__(
        self,
        *,
        url: str = _DEFAULT_STATUS_URL,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._url = url
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._last_refresh: float = 0.0
        self._cache: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, site: str) -> dict[str, Any] | None:
        """Return the status dict for *site*, or ``None`` if unknown.

        Refreshes from the remote when the cached data is stale
        (older than ``poll_interval`` seconds).  Any refresh error is
        swallowed — the last-known-good data (or empty cache) is
        returned instead.

        Thread-safe: the lock ensures only one refresh happens per
        poll interval even when N concurrent requests arrive after
        expiry (ultrareview C).
        """
        with self._lock:
            now = time.monotonic()
            if now - self._last_refresh >= self._poll_interval:
                # Stamp the time BEFORE the network call so any thread that
                # acquires the lock while _refresh() is running sees a fresh
                # enough timestamp and skips the redundant fetch.
                self._last_refresh = now
                do_refresh = True
            else:
                do_refresh = False
        if do_refresh:
            self._refresh()
        return self._cache.get(site)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """Attempt a remote fetch and update the cache.  Never raises.

        Note: ``_last_refresh`` is stamped by ``get()`` *before* this method
        is called so that concurrent callers see the fresh timestamp and skip
        the redundant fetch (ultrareview C).  We do NOT update it here.
        """
        try:
            data = self._fetch_remote()
            if isinstance(data, dict):
                self._cache = data
                log.debug("open_slum: refreshed %d sites", len(self._cache))
            else:
                log.debug("open_slum: unexpected response type %s (expected dict)", type(data).__name__)
        except Exception as exc:  # noqa: BLE001
            log.debug("open_slum: refresh failed: %s", exc)

    def _fetch_remote(self) -> dict[str, Any]:
        """Fetch and return the remote status JSON.

        Returns a dict keyed by site name.  Raises on any HTTP / network
        error so that ``_refresh`` can swallow and log it.
        """
        import httpx  # deferred so import errors are surfaced lazily

        with httpx.Client(timeout=10.0) as client:
            resp = client.get(self._url)
            resp.raise_for_status()
            return resp.json()
