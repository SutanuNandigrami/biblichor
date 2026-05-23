"""Welib via Playwright: launch real headless Chromium, click the "Read"
(span.preview[data-book-url]) element, observe network responses, capture the
first URL whose path looks like a book payload.

Welib's `/fast_preview/` endpoint is Cloudflare-Turnstile-gated. Headless
Chromium triggers Turnstile and fails. We work around this by pre-warming
the CF cookie jar via FlareSolverr (which the project already uses to clear
CF on welib search/md5 pages) and injecting those cookies into the Playwright
context before navigating.

Search reuses WelibCurl (FlareSolverr-backed) — only resolve_cdn changes.
"""

from __future__ import annotations

import logging
import time

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle
from endless_library.scrapers.welib_cookies import parse_cookie_string
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
    if _is_book_payload_url(url):
        return True
    if content_type:
        ct = content_type.lower()
        if "epub" in ct or "pdf" in ct or "octet-stream" in ct:
            return not ("html" in ct or "json" in ct or "image" in ct)
    return False


def _fs_cookies_to_playwright(fs_cookies: list[dict]) -> list[dict]:
    out: list[dict] = []
    for c in fs_cookies or []:
        entry: dict = {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain") or ".welib.org",
            "path": c.get("path") or "/",
        }
        if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0:
            entry["expires"] = int(c["expires"])
        if c.get("httpOnly") is not None:
            entry["httpOnly"] = bool(c["httpOnly"])
        if c.get("secure") is not None:
            entry["secure"] = bool(c["secure"])
        ss = c.get("sameSite")
        if ss in ("Strict", "Lax", "None"):
            entry["sameSite"] = ss
        out.append(entry)
    return out


class WelibPlaywright(WelibCurl):
    """search() inherited from WelibCurl; resolve_cdn uses Playwright."""

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
            from patchright.sync_api import TimeoutError as PWTimeout
            from patchright.sync_api import sync_playwright
        except ImportError:
            log.warning("playwright not installed; install with: pip install playwright")
            return None

        # Pre-warm: get CF clearance cookies via FlareSolverr by hitting /md5/<hash>
        cf_cookies = self._cf_warm_cookies(candidate.md5)
        if not cf_cookies:
            log.info("welib-pw: no CF cookies; /fast_preview/ will likely 403")

        url = f"{_WELIB_BASE}/md5/{candidate.md5}"
        captured: list[str] = []

        def on_response(resp):
            try:
                u = resp.url
                ct = resp.headers.get("content-type") if resp.headers else None
                if _looks_like_book_response(u, ct):
                    log.info("welib-pw: captured response %s (ct=%s)", u[:100], ct)
                    captured.append(u)
            except Exception as e:
                log.debug("welib-pw on_response error: %s", e)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            auth_cookies = parse_cookie_string(getattr(self.cfg, "welib_auth_cookie", None) or "")
            if cf_cookies or auth_cookies:
                try:
                    cookies = _fs_cookies_to_playwright(cf_cookies) + auth_cookies
                    context.add_cookies(cookies)
                    log.info(
                        "welib-pw: injected %d CF + %d auth cookies",
                        len(cf_cookies),
                        len(auth_cookies),
                    )
                except Exception as e:
                    log.warning("welib-pw cookie inject failed: %s", e)
            page = context.new_page()
            page.on("response", on_response)

            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.wait_timeout_ms,
                )
            except PWTimeout:
                log.warning("welib-pw: md5 page nav timeout")
                browser.close()
                return None
            except Exception as e:
                log.warning("welib-pw nav error: %s", e)
                browser.close()
                return None

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

            deadline = time.monotonic() + (self.wait_timeout_ms / 1000)
            while time.monotonic() < deadline and not captured:
                page.wait_for_timeout(500)

            browser.close()

        if captured:
            return DownloadHandle(url=captured[0], headers={}, expected_filename=None)
        log.info("welib-pw: no book URL captured within timeout")
        return None

    def _cf_warm_cookies(self, md5: str) -> list[dict]:
        """Use FlareSolverr to GET /md5/<hash> and return the resulting cookies."""
        from endless_library.flaresolverr import FlareSolverr, FlareSolverrError

        fs = self._fs
        if fs is None:
            fs = FlareSolverr(self.cfg.flaresolverr_url, max_timeout_ms=60_000)
            self._fs = fs
        try:
            r = fs.get(f"{_WELIB_BASE}/md5/{md5}")
        except FlareSolverrError as e:
            log.info("welib-pw: FS warm-up failed: %s", e)
            return []
        except Exception as e:
            log.info("welib-pw: FS warm-up error: %s", e)
            return []
        return list(r.cookies or [])
