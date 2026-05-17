"""Welib via Playwright: launch real headless Chromium, click the "Read"
(span.preview[data-book-url]) element, observe network responses, capture the
first URL whose path looks like a book payload.

This is the only path that reliably retrieves the book file from welib for
copyrighted titles where IPFS is taken down — because welib's viewer pulls
the file via JS after a click that mutates iframe src + makes XHRs to a
backend the static HTML never reveals.

Search reuses WelibCurl (FlareSolverr-backed) — only resolve_cdn changes.
"""

from __future__ import annotations

import logging
import time

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle
from endless_library.scrapers.welib_curl import (
    WelibCurl,
    _is_book_payload_url,
)

log = logging.getLogger(__name__)


_WELIB_BASE = "https://welib.org"


def _looks_like_book_response(url: str, content_type: str | None) -> bool:
    """Match a network response that's likely a book payload."""
    lc = url.lower()
    if "/covers/" in lc:
        return False
    # Use the existing helper which checks ext + filename hints + ipfs
    if _is_book_payload_url(url):
        return True
    # content-type fallback
    if content_type:
        ct = content_type.lower()
        if "epub" in ct or "pdf" in ct or "octet-stream" in ct:
            # avoid html/json
            return not ("html" in ct or "json" in ct or "image" in ct)
    return False


class WelibPlaywright(WelibCurl):
    """Inherits search() from WelibCurl, overrides resolve_cdn() with Playwright."""

    name = "welib_playwright"
    provider = "welib"

    def __init__(
        self,
        cfg: ScrapersCfg,
        *,
        http_get=None,
        flaresolverr=None,
        wait_timeout_ms: int = 30_000,
    ) -> None:
        super().__init__(cfg, http_get=http_get, flaresolverr=flaresolverr)
        self.wait_timeout_ms = wait_timeout_ms

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.md5:
            return None
        try:
            from playwright.sync_api import TimeoutError as PWTimeout
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright not installed; install with: pip install playwright")
            return None

        url = f"{_WELIB_BASE}/md5/{candidate.md5}"
        captured: list[str] = []
        captured_at: list[float] = []

        def on_response(resp):
            try:
                u = resp.url
                ct = resp.headers.get("content-type") if resp.headers else None
                if _looks_like_book_response(u, ct):
                    log.info("welib-pw: captured response %s (ct=%s)", u[:100], ct)
                    captured.append(u)
                    captured_at.append(time.monotonic())
            except Exception as e:
                log.debug("welib-pw on_response error: %s", e)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page = context.new_page()
            page.on("response", on_response)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.wait_timeout_ms)
            except PWTimeout:
                log.warning("welib-pw: md5 page nav timeout")
                browser.close()
                return None
            except Exception as e:
                log.warning("welib-pw nav error: %s", e)
                browser.close()
                return None

            # Find the Read button
            read_selectors = [
                "span.preview[data-book-url]",
                'a[href*="fast_view"]',
                'a[href*="fast_preview"]',
            ]
            clicked = False
            for sel in read_selectors:
                try:
                    el = page.locator(sel).first
                    if el.count():
                        el.click(timeout=5_000)
                        log.info("welib-pw: clicked %s", sel)
                        clicked = True
                        break
                except Exception as e:
                    log.debug("welib-pw click %s failed: %s", sel, e)
            if not clicked:
                log.info("welib-pw: no Read button on this page")
                browser.close()
                return None

            # Wait up to wait_timeout_ms for a book payload response.
            deadline = time.monotonic() + (self.wait_timeout_ms / 1000)
            while time.monotonic() < deadline and not captured:
                page.wait_for_timeout(500)

            browser.close()

        if captured:
            return DownloadHandle(url=captured[0], headers={}, expected_filename=None)
        log.info("welib-pw: no book URL captured within timeout")
        return None
