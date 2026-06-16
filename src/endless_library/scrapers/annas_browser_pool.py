"""Process-wide persistent Chromium pool for AnnasArchivePatchright.

Launching Chromium costs ~600 ms on this host (Node subprocess + browser
binary). With a fresh launch per resolve_cdn call we eat that cost N
times across a batch; pooling collapses it to once per recycle window.

We do NOT also share the BrowserContext across resolves. An earlier
revision pooled the context too on the theory that the cached __ddg*
cookies would let later resolves skip warmup. Bench reality: shared
context state crossed enough request signals that annas started
returning slow_download pages with no partner URL embedded, dropping
success rate from 100% to 80%. So each resolve still gets a fresh
context (~50 ms cost) but skips the ~600 ms browser launch.

Recycle after _MAX_USES_PER_BROWSER resolves so memory doesn't grow
without bound. atexit teardown on process exit.

Concurrency: patchright sync_api is not thread-safe. APScheduler runs
scrape jobs serially today, so the lock just serialises spawn/teardown.
PR #41 (parallel browser contexts) will fan out multiple contexts on
the same pooled browser; the lock model survives.
"""

from __future__ import annotations

import atexit
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchright.sync_api import Browser, Playwright

log = logging.getLogger(__name__)

_MAX_USES_PER_BROWSER = 50

_CHROMIUM_LAUNCH_ARGS: tuple[str, ...] = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
)


def _default_launcher(headless: bool) -> tuple[Playwright, Browser]:
    """Real patchright launch. Imported lazily so test environments
    without patchright installed can still import this module."""
    from patchright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless, args=list(_CHROMIUM_LAUNCH_ARGS))
    return pw, browser


class BrowserPool:
    def __init__(
        self,
        *,
        max_uses: int = _MAX_USES_PER_BROWSER,
        headless: bool = True,
        launcher: Callable[[bool], tuple[object, object]] | None = None,
    ) -> None:
        self._max_uses = max_uses
        self._headless = headless
        self._launcher = launcher or _default_launcher  # type: ignore[assignment]
        self._pw: object | None = None
        self._browser: object | None = None
        self._uses = 0
        self._lock = threading.Lock()

    def acquire(self) -> object:
        """Return a live Browser, spawning or recycling as needed.

        Caller is responsible for closing the context it creates from
        the browser (`browser.new_context().close()`), but MUST NOT
        close the browser itself.
        """
        with self._lock:
            if self._browser is None or self._uses >= self._max_uses:
                if self._browser is not None:
                    log.info("BrowserPool: recycling Chromium after %d uses", self._uses)
                self._teardown_locked()
                self._spawn_locked()
            self._uses += 1
            return self._browser  # type: ignore[return-value]

    def report_failure(self) -> None:
        """Caller saw a browser-level error (PWError, crash, etc.).
        Tear down so the next acquire spawns a fresh browser."""
        with self._lock:
            log.info("BrowserPool: failure reported; tearing down")
            self._teardown_locked()

    def shutdown(self) -> None:
        with self._lock:
            self._teardown_locked()

    @property
    def uses(self) -> int:
        with self._lock:
            return self._uses

    @property
    def is_alive(self) -> bool:
        with self._lock:
            return self._browser is not None

    def _spawn_locked(self) -> None:
        self._pw, self._browser = self._launcher(self._headless)  # type: ignore[misc]
        self._uses = 0
        log.info("BrowserPool: launched fresh Chromium")

    def _teardown_locked(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._pw = None
        self._uses = 0


_pool_singleton: BrowserPool | None = None
_singleton_lock = threading.Lock()


def get_default_pool() -> BrowserPool:
    """Lazy module-level singleton. atexit-registered for clean shutdown."""
    global _pool_singleton
    with _singleton_lock:
        if _pool_singleton is None:
            _pool_singleton = BrowserPool()
            atexit.register(_pool_singleton.shutdown)
        return _pool_singleton
