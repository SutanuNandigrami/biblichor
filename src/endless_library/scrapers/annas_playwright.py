"""Anna's Archive via vanilla Playwright Chromium.

For Anna's, plain Playwright works fine on /search and /md5/ pages (no
Turnstile on those endpoints). It's a sensible escalation when curl-cffi
or FlareSolverr have already burned their rate-limit budget on this IP.
"""

from __future__ import annotations

import logging
import random
import time

from endless_library.config import ScrapersCfg
from endless_library.scrapers.annas_curl import AnnasArchiveCurl

log = logging.getLogger(__name__)


class AnnasArchivePlaywright(AnnasArchiveCurl):
    """Inherits parsing/scoring from AnnasArchiveCurl; only the HTTP transport
    changes — we render every page in headless Chromium so JS-gated content
    and CF-protected pages both work."""

    name = "annas_playwright"
    provider = "annas"

    def __init__(self, cfg: ScrapersCfg, *, headless: bool = True) -> None:
        super().__init__(cfg)
        self.headless = headless

    def _get(self, url: str) -> str | None:
        try:
            from playwright.sync_api import TimeoutError as PWTimeout
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright not installed; install with: pip install playwright")
            return None

        sleep_s = self.bucket.acquire(url)
        if sleep_s > 0:
            time.sleep(sleep_s)
        else:
            time.sleep(self.cfg.request_delay_seconds + random.uniform(0.3, 1.0))

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                )
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except PWTimeout:
                    log.warning("playwright nav timeout: %s", url)
                    browser.close()
                    return None
                html = page.content()
                self._last_status = 200
                browser.close()
                return html
        except Exception as e:
            log.warning("annas_playwright error %s: %s", url, e)
            self._last_status = None
            return None
