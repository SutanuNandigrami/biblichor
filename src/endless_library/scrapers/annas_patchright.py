"""Annas Archive via patchright (stealth Playwright).

annas-archive.gl's `/slow_download/<md5>/0/<N>` endpoint moved from
Cloudflare to DDoS-Guard sometime in mid-2026. Neither FlareSolverr nor
the sarperavci/CloudflareBypassForScraping sidecar handles DDG: their
bypass loops look for Cloudflare-specific markers ("Just a moment...",
the Turnstile checkbox iframe) and exit as a no-op when the page is
DDG. The /md5/<hash> page is reachable via cf-bypass for the metadata,
but the actual download endpoint stays 403.

Patchright is a hardened fork of Playwright whose Chromium evades the
fingerprint checks DDG uses (CDP detection, automation indicators,
WebGL/UA mismatch). In a real headless Chromium driven by patchright,
DDG's auto-resolve JS completes in ~5-10s and the slow_download page
loads normally. We then read the page for the partner-CDN URL
(`https://b4mcx2ml.net/d3/...` or similar) and hand it back as a
DownloadHandle.

Browser strategy:
  - One Chromium PER resolve_cdn call (~150 MB RAM, ~5s warmup).
  - Warm up on /  to seed DDG cookies, then navigate to slow_download.
  - Poll page title for up to 20s for DDG to clear.
  - Find the "Download now" or partner-CDN URL in the resolved HTML.
  - browser.close() on every exit path (try/finally).

Falls back to None (chain continues to next scraper) on any failure —
patchright may not be installed in test environments and the Chromium
binary is only present in the production image. The base AnnasArchive-
Curl handles parsing/scoring; we only override resolve_cdn.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle
from endless_library.scrapers.annas_curl import AnnasArchiveCurl
from endless_library.scrapers.annas_ddg_cookies import DDGCookieCache

log = logging.getLogger(__name__)

# Hoisted so the cookie-replay fast path can reuse the exact UA the
# browser was minted with. DDG ties cookies to the UA they were issued
# under; replaying with a different UA produces a fresh challenge.
_CHROMIUM_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
# DDG sets multiple cookies; their names are __ddg1_, __ddg2_, __ddg5_,
# __ddg8_, etc. with mirror-specific random suffixes. Capture every
# cookie starting with this prefix.
_DDG_COOKIE_PREFIX = "__ddg"
# Default location for the persistent cookie cache. /data is the
# bind-mounted volume that already hosts library.db, so it survives
# container restarts and is writable by the biblichor user.
_DEFAULT_COOKIE_CACHE_PATH = Path("/data/annas_ddg_cookies.json")

# Process-wide cache singleton. Lazy-init so unit tests don't touch
# /data on import.
_cookie_cache_singleton: DDGCookieCache | None = None


def _default_cookie_cache() -> DDGCookieCache:
    global _cookie_cache_singleton
    if _cookie_cache_singleton is None:
        path = _DEFAULT_COOKIE_CACHE_PATH if _DEFAULT_COOKIE_CACHE_PATH.parent.is_dir() else None
        _cookie_cache_singleton = DDGCookieCache(path=path)
    return _cookie_cache_singleton


# Direct partner CDN URLs annas embeds on the slow_download page. The
# d3 path is annas's own libgen-mirror partner; .xyz/.net/.cc are
# specific physical mirrors. Anything containing `/d3/` is a partner
# direct link, signed (the `~/...` segment is a per-session token).
_PARTNER_CDN_RE = re.compile(
    r'https?://[a-zA-Z0-9.-]+/d3/[^\s"\'<>]+',
)
# Anchor whose visible text reads "Download now", "📚 Download now",
# "Download with short filename" etc — the partner-resolved button.
# Allow up to 100 chars of inline content (text, emoji, nested <span>s)
# between the opening tag and the recognisable phrase. DOTALL because
# Annas inserts \n in the rendered anchor body.
_DOWNLOAD_ANCHOR_RE = re.compile(
    r'<a[^>]+href="(https?://[^"]+)"[^>]*>'
    r"(?:[^<]|<[^a/])*?"
    r"(?:Download now|Download with short)",
    re.IGNORECASE | re.DOTALL,
)
# DDoS-Guard interstitial title.
_DDG_TITLE = re.compile(r"ddos-guard", re.IGNORECASE)
# Slow-download countdown indicator (free-tier wait).
_COUNTDOWN_RE = re.compile(r"js-partner-countdown[^>]*>\s*(\d+)")


class AnnasArchivePatchright(AnnasArchiveCurl):
    """Use a stealth-Chromium to drive the slow_download flow that DDG
    blocks for plain HTTP clients. Inherits search/parse/score from
    AnnasArchiveCurl; only resolve_cdn is overridden."""

    name = "annas_patchright"
    provider = "annas"

    def __init__(
        self,
        cfg: ScrapersCfg,
        *,
        headless: bool = True,
        cookie_cache: DDGCookieCache | None = None,
    ) -> None:
        super().__init__(cfg)
        self.headless = headless
        # Slightly larger DDG-resolve budget than the parent's vanilla
        # _ipfs_reachable; the JS challenge regularly takes 5-8 s.
        self._ddg_resolve_seconds = 25
        # How long to wait for the slow_download free-tier countdown.
        # Anonymous users get 60-120 s; donors get 0. Cap at 130 so
        # we don't dangle a Chromium forever on a hostile shard.
        self._countdown_max_seconds = 130
        self._cookie_cache = cookie_cache or _default_cookie_cache()

    def _try_cookie_replay(self, slow_url: str) -> DownloadHandle | None:
        """Plain httpx GET of /slow_download/... with cached DDG cookies.

        Returns a DownloadHandle if the page renders cleanly and exposes
        a partner CDN URL; None otherwise. On every failure mode (stale
        cookies, 4xx, DDG re-presentation, no anchor) we invalidate the
        cache entry so the next call falls back to a fresh patchright
        run rather than re-using broken cookies.
        """
        entry = self._cookie_cache.get(self.mirrors.current)
        if entry is None:
            return None
        try:
            import httpx
        except ImportError:
            return None
        try:
            with httpx.Client(
                timeout=20,
                cookies=entry.cookies,
                headers={"User-Agent": entry.user_agent},
                follow_redirects=True,
            ) as c:
                r = c.get(slow_url)
        except Exception as e:
            log.info("annas_patchright cookie_replay: fetch error: %s; invalidating", e)
            self._cookie_cache.invalidate(self.mirrors.current)
            return None
        if r.status_code != 200:
            log.info(
                "annas_patchright cookie_replay: HTTP %s; invalidating",
                r.status_code,
            )
            self._cookie_cache.invalidate(self.mirrors.current)
            return None
        html = r.text
        title_m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if title_m and _DDG_TITLE.search(title_m.group(1)):
            log.info("annas_patchright cookie_replay: hit DDG; invalidating")
            self._cookie_cache.invalidate(self.mirrors.current)
            return None
        m = _DOWNLOAD_ANCHOR_RE.search(html)
        if m:
            return DownloadHandle(url=m.group(1), headers={}, expected_filename=None)
        m2 = _PARTNER_CDN_RE.search(html)
        if m2:
            return DownloadHandle(url=m2.group(0), headers={}, expected_filename=None)
        log.info("annas_patchright cookie_replay: no partner URL in page")
        return None

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        md5 = candidate.md5
        if not md5:
            return None

        # Light token-bucket pressure: patchright is heavy. We share the
        # parent's bucket so it counts against the same Annas rate
        # budget as curl/FS variants.
        sleep_s = self.bucket.acquire(f"{self.mirrors.current}/slow_download/{md5}")
        if sleep_s > 0:
            time.sleep(sleep_s)

        slow_url = f"{self.mirrors.current}/slow_download/{md5}/0/0"
        warm_url = f"{self.mirrors.current}/"

        # FAST PATH: if we have cached DDG cookies from a prior browser
        # session, try plain httpx first. 5-30 s of Chromium dance
        # becomes a single ~1-3 s HTTP round-trip on cache-hit.
        handle = self._try_cookie_replay(slow_url)
        if handle is not None:
            log.info("annas_patchright: cookie_replay HIT (%s)", handle.url[:80])
            return handle

        try:
            from patchright.sync_api import (
                Error as PWError,
            )
            from patchright.sync_api import (
                TimeoutError as PWTimeout,
            )
            from patchright.sync_api import (
                sync_playwright,
            )
        except ImportError:
            log.warning("annas_patchright: patchright not installed; skipping")
            return None

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=self.headless,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                try:
                    ctx = browser.new_context(
                        user_agent=_CHROMIUM_UA,
                        viewport={"width": 1920, "height": 1080},
                        locale="en-US",
                    )
                    page = ctx.new_page()

                    # 1) Warm up on root domain so DDG cookies get set in this
                    #    browser context. DDG sometimes 403s slow_download
                    #    immediately if the client has no prior site visit.
                    try:
                        page.goto(warm_url, wait_until="networkidle", timeout=30_000)
                    except PWTimeout:
                        log.info("annas_patchright: warmup timed out, continuing")

                    # 2) Navigate to the actual slow_download page.
                    try:
                        page.goto(
                            slow_url,
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                    except PWTimeout:
                        log.warning("annas_patchright: slow_download goto timed out")
                        return None

                    # 3) Wait for DDG's JS interstitial to clear. Real
                    #    Chromium running JS finishes the challenge in
                    #    5-8 s; we poll the title up to _ddg_resolve_seconds.
                    deadline = time.time() + self._ddg_resolve_seconds
                    while time.time() < deadline:
                        title = (page.title() or "").lower()
                        if not _DDG_TITLE.search(title):
                            break
                        time.sleep(1.5)
                    else:
                        log.warning(
                            "annas_patchright: DDG didn't clear within %ds",
                            self._ddg_resolve_seconds,
                        )
                        return None

                    # Capture the post-challenge __ddg* cookies for future
                    # replay before we sleep through any countdown. They
                    # are valid as soon as DDG releases the page, and we
                    # want to seed the cache even if a later step fails.
                    try:
                        raw_cookies = ctx.cookies()
                        ddg_cookies = {
                            c["name"]: c["value"]
                            for c in raw_cookies
                            if c.get("name", "").startswith(_DDG_COOKIE_PREFIX)
                        }
                        if ddg_cookies:
                            self._cookie_cache.set(self.mirrors.current, ddg_cookies, _CHROMIUM_UA)
                            log.info(
                                "annas_patchright: cached %d DDG cookies for %s",
                                len(ddg_cookies),
                                self.mirrors.current,
                            )
                    except Exception as e:
                        log.debug("annas_patchright: cookie capture failed: %s", e)

                    # DDG cleared but the partner page is still rendering;
                    # the "Download now" link is JS-injected after a quick
                    # client-side hop. Wait for networkidle so the DOM is
                    # actually stable before extracting URLs. Cap at 15 s —
                    # long enough for slow shards, short enough not to dangle
                    # Chromium on hostile partners.
                    try:
                        page.wait_for_load_state("networkidle", timeout=15_000)
                    except PWTimeout:
                        pass

                    # 4) If the page has a countdown timer (free-tier wait),
                    #    sleep through it before trying to extract a CDN URL.
                    html = page.content()
                    cd = _COUNTDOWN_RE.search(html)
                    if cd:
                        wait_s = min(int(cd.group(1)) + 3, self._countdown_max_seconds)
                        log.info(
                            "annas_patchright: countdown %ds detected; waiting",
                            wait_s,
                        )
                        time.sleep(wait_s)
                        try:
                            page.wait_for_load_state("networkidle", timeout=15_000)
                        except PWTimeout:
                            pass
                        html = page.content()

                    # 5) Extract the partner CDN URL. Prefer the anchored
                    #    "Download now" link because it's exactly what the
                    #    user-facing button would click; fall back to any
                    #    partner /d3/ URL in the page body.
                    m = _DOWNLOAD_ANCHOR_RE.search(html)
                    if m:
                        cdn_url = m.group(1)
                        log.info(
                            "annas_patchright: picked download anchor: %s",
                            cdn_url[:80],
                        )
                        return DownloadHandle(url=cdn_url, headers={}, expected_filename=None)
                    m2 = _PARTNER_CDN_RE.search(html)
                    if m2:
                        cdn_url = m2.group(0)
                        log.info(
                            "annas_patchright: picked first partner /d3/: %s",
                            cdn_url[:80],
                        )
                        return DownloadHandle(url=cdn_url, headers={}, expected_filename=None)

                    log.warning("annas_patchright: no CDN URL found on resolved page")
                    return None
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass

        except PWError as e:
            log.warning("annas_patchright: playwright error: %s", e)
            return None
        except Exception as e:
            log.warning("annas_patchright: unexpected: %s", e)
            return None
