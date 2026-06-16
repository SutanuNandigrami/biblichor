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

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle
from endless_library.scrapers.annas_curl import AnnasArchiveCurl

log = logging.getLogger(__name__)

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
    r'(?:[^<]|<[^a/])*?'
    r'(?:Download now|Download with short)',
    re.IGNORECASE | re.DOTALL,
)
# DDoS-Guard interstitial title.
_DDG_TITLE = re.compile(r"ddos-guard", re.IGNORECASE)
# Slow-download countdown indicator (free-tier wait).
_COUNTDOWN_RE = re.compile(r'js-partner-countdown[^>]*>\s*(\d+)')


class AnnasArchivePatchright(AnnasArchiveCurl):
    """Use a stealth-Chromium to drive the slow_download flow that DDG
    blocks for plain HTTP clients. Inherits search/parse/score from
    AnnasArchiveCurl; only resolve_cdn is overridden."""

    name = "annas_patchright"
    provider = "annas"

    def __init__(self, cfg: ScrapersCfg, *, headless: bool = True) -> None:
        super().__init__(cfg)
        self.headless = headless
        # Slightly larger DDG-resolve budget than the parent's vanilla
        # _ipfs_reachable; the JS challenge regularly takes 5-8 s.
        self._ddg_resolve_seconds = 25
        # How long to wait for the slow_download free-tier countdown.
        # Anonymous users get 60-120 s; donors get 0. Cap at 130 so
        # we don't dangle a Chromium forever on a hostile shard.
        self._countdown_max_seconds = 130

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        md5 = candidate.md5
        if not md5:
            return None

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

        # Light token-bucket pressure: patchright is heavy. We share the
        # parent's bucket so it counts against the same Annas rate
        # budget as curl/FS variants.
        sleep_s = self.bucket.acquire(f"{self.mirrors.current}/slow_download/{md5}")
        if sleep_s > 0:
            time.sleep(sleep_s)

        slow_url = f"{self.mirrors.current}/slow_download/{md5}/0/0"
        warm_url = f"{self.mirrors.current}/"

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
                        user_agent=(
                            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
                        ),
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
                        return DownloadHandle(
                            url=cdn_url, headers={}, expected_filename=None
                        )
                    m2 = _PARTNER_CDN_RE.search(html)
                    if m2:
                        cdn_url = m2.group(0)
                        log.info(
                            "annas_patchright: picked first partner /d3/: %s",
                            cdn_url[:80],
                        )
                        return DownloadHandle(
                            url=cdn_url, headers={}, expected_filename=None
                        )

                    log.warning(
                        "annas_patchright: no CDN URL found on resolved page"
                    )
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
