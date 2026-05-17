"""Anna's Archive via CloakBrowser — Chromium with C++ source-level stealth.

Drop-in Playwright API. Same Anna's parser. Use when standard Playwright
gets fingerprinted (rare on Anna's specifically but useful insurance).
"""

from __future__ import annotations

import logging
import random
import time

from endless_library.config import ScrapersCfg
from endless_library.scrapers.annas_curl import AnnasArchiveCurl

log = logging.getLogger(__name__)


class AnnasArchiveCloakBrowser(AnnasArchiveCurl):
    name = "annas_cloakbrowser"
    provider = "annas"

    def __init__(self, cfg: ScrapersCfg, *, headless: bool = True, humanize: bool = True) -> None:
        super().__init__(cfg)
        self.headless = headless
        self.humanize = humanize

    def _get(self, url: str) -> str | None:
        try:
            from cloakbrowser import launch
        except ImportError:
            log.warning("cloakbrowser not installed; install with: pip install cloakbrowser")
            return None
        from playwright.sync_api import TimeoutError as PWTimeout

        sleep_s = self.bucket.acquire(url)
        if sleep_s > 0:
            time.sleep(sleep_s)
        else:
            time.sleep(self.cfg.request_delay_seconds + random.uniform(0.3, 1.0))

        try:
            kwargs: dict = {"headless": self.headless}
            try:
                browser = launch(humanize=self.humanize, **kwargs)
            except TypeError:
                browser = launch(**kwargs)
            ctx = browser.new_context()
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except PWTimeout:
                log.warning("cloakbrowser nav timeout: %s", url)
                browser.close()
                return None
            html = page.content()
            self._last_status = 200
            browser.close()
            return html
        except Exception as e:
            log.warning("annas_cloakbrowser error %s: %s", url, e)
            self._last_status = None
            return None
