from __future__ import annotations

import contextlib
import logging
import random
import re
import time
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.domain.scoring import _is_non_latin as _query_is_non_latin
from endless_library.scrapers import annas_domains as _annas_domains
from endless_library.scrapers import annas_parsing as _annas_parsing  # shared parser
from endless_library.scrapers.base import ANNAS_CDN_REGEX, url_has_book_ext
from endless_library.scrapers.http_client import BIBLICHOR_USER_AGENT
from endless_library.scrapers.rate_limit import MirrorRotator, TokenBucket

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


class AnnasArchiveCurl:
    """Anna's Archive scraper using curl-cffi for Chrome TLS impersonation."""

    name = "annas_curl"
    provider = "annas"

    def __init__(
        self,
        cfg: ScrapersCfg,
        *,
        http_get: Any | None = None,  # callable(url, *, headers=...) -> (status, text)
    ) -> None:
        self.cfg = cfg
        self.mirrors = MirrorRotator(cfg.annas_mirrors or ["https://annas-archive.gl"])
        self.bucket = TokenBucket(capacity=6, period_seconds=60)
        self._http_get = http_get

    # ----- Public API -----

    def search(self, query: SearchQuery) -> list[Candidate]:
        candidates: list[Candidate] = []
        for fmt in query.format_priority:
            # If the query title is non-Latin (Bengali/CJK/Devanagari/...),
            # drop the lang hint so Anna's doesn't pad results with English
            # fallbacks.
            params: dict[str, str] = {
                "q": f"{query.title} {query.author or ''}".strip(),
                "ext": fmt,
                "sort": "",
            }
            if not _query_is_non_latin(query.title or ""):
                params["lang"] = query.language
            qs = "&".join(f"{k}={quote_plus(str(v))}" for k, v in params.items())
            url = f"{self.mirrors.current}/search?{qs}"
            html = self._get(url)
            if html is None:
                continue
            candidates.extend(self._parse_search_results(html, fmt))
            if candidates:
                break
        return candidates

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        md5 = candidate.md5
        if not md5:
            return None
        # 1. fetch md5 page → slow_download URL
        md5_url = f"{self.mirrors.current}/md5/{md5}"
        html = self._get(md5_url)
        if not html:
            return None
        slow_url = self._extract_slow_download(html)
        if not slow_url:
            # Maybe the md5 page itself has the CDN already
            direct = self._first_cdn(html)
            if direct:
                return DownloadHandle(url=direct, headers={}, expected_filename=None)
            return None
        slow_url = urljoin(self.mirrors.current, slow_url)
        # 2. poll slow_download page
        handle = self._poll_slow_download(slow_url)
        if handle:
            return handle
        # Phase 6s.2: Wayback CDX last-resort recovery for dead md5 pages
        try:
            from endless_library.scrapers.wayback_fallback import recover_links

            recovered = recover_links(md5)
            if recovered:
                log.info(
                    "annas_curl: Wayback CDX recovered %d link(s) for md5=%s",
                    len(recovered),
                    md5,
                )
                return recovered[0]
        except Exception as e:
            log.info("annas_curl: wayback fallback failed: %s", e)
        return None

    # ----- Internals -----

    def _get(self, url: str) -> str | None:
        from urllib.parse import urlparse as _urlparse

        sleep = self.bucket.acquire(url)
        if sleep > 0:
            log.debug("rate-limit sleep %.1fs for %s", sleep, url)
            time.sleep(sleep)
        else:
            time.sleep(self.cfg.request_delay_seconds + random.uniform(0.5, 2.0))
        host = _urlparse(url).netloc
        self._last_host = host
        if self._http_get is not None:
            status, text = self._http_get(url, headers=DEFAULT_HEADERS)
        else:
            from curl_cffi import requests as cf

            try:
                r = cf.get(url, headers=DEFAULT_HEADERS, impersonate="chrome120", timeout=30)
                status, text = r.status_code, r.text
                # Phase 6w.2: retry once on gateway errors with next mirror
                if status in (502, 503, 504):
                    _annas_domains.mark_cool(host)
                    next_host = _annas_domains.next_mirror()
                    retry_url = url.replace(host, next_host, 1)
                    host = next_host
                    self._last_host = next_host
                    log.info("annas_curl: gateway %d, retrying with %s", status, next_host)
                    r = cf.get(
                        retry_url, headers=DEFAULT_HEADERS, impersonate="chrome120", timeout=30
                    )
                    status, text = r.status_code, r.text
            except Exception as e:
                log.warning("curl_cffi failed for %s: %s", url, e)
                _annas_domains.mark_cool(host)
                self._last_status = None
                return None
        self._last_status = status
        if status == 200:
            _annas_domains.mark_success(host)
            return text
        log.warning("HTTP %d for %s", status, url)
        if status in (403, 429, 502, 503, 504):
            _annas_domains.mark_cool(host)
            self.mirrors.next_after_failure()
        return None

    def _parse_search_results(self, html: str, fmt_hint: str) -> list[Candidate]:
        """Delegate to the shared annas_parsing module.

        See annas_parsing.py for extraction logic (canonical).
        annas_cloakbrowser.py also uses the same shared parser.
        """
        return _annas_parsing.parse_search_results(
            html,
            self.mirrors.current,
            fmt_hint=fmt_hint,
        )

    @staticmethod
    def _extract_isbns(text: str) -> list[str]:
        """Pull every plausible ISBN-13 (and ISBN-10, normalized to 13) from a string."""
        import re as _re

        found: list[str] = []
        # ISBN-13: starts with 978 or 979, then 10 more digits. Allow hyphens.
        for m in _re.finditer(r"\b(97[89][-\s]?(?:\d[-\s]?){9}\d)\b", text):
            digits = "".join(c for c in m.group(1) if c.isdigit())
            if len(digits) == 13:
                found.append(digits)
        # ISBN-10: 10 chars, last may be X. Convert to ISBN-13.
        for m in _re.finditer(r"(?<!\d)((?:\d[-\s]?){9}[\dXx])(?!\d)", text):
            raw = m.group(1)
            cleaned = "".join(c for c in raw if c.isdigit() or c in "Xx")
            if len(cleaned) != 10:
                continue
            body = "978" + cleaned[:9]
            chk = (
                10 - sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(body)) % 10
            ) % 10
            found.append(body + str(chk))
        # Dedup, preserve order
        seen: set[str] = set()
        out: list[str] = []
        for x in found:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    @staticmethod
    def _parse_format(s: str) -> str | None:
        m = re.search(r"\b(epub|pdf|mobi|azw3|djvu|fb2|cbz|cbr|doc|docx|rtf|txt|lit)\b", s, re.I)
        return m.group(1).lower() if m else None

    @staticmethod
    def _parse_language(s: str) -> str | None:
        m = re.search(
            r"\b(English|German|Spanish|French|Italian|Russian|Portuguese|Chinese|Japanese|Hindi|Bengali)\b",
            s,
            re.I,
        )
        if not m:
            return None
        mapping = {
            "english": "en",
            "german": "de",
            "spanish": "es",
            "french": "fr",
            "italian": "it",
            "russian": "ru",
            "portuguese": "pt",
            "chinese": "zh",
            "japanese": "ja",
            "hindi": "hi",
            "bengali": "bn",
        }
        return mapping.get(m.group(1).lower())

    @staticmethod
    def _parse_year(s: str) -> int | None:
        m = re.search(r"\b(19|20)\d{2}\b", s)
        return int(m.group(0)) if m else None

    @staticmethod
    def _extract_slow_download(html: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        a = soup.select_one('a[href*="/slow_download/"]')
        return a["href"] if a else None

    def _poll_slow_download(self, url: str) -> DownloadHandle | None:
        deadline = time.time() + self.cfg.slow_download_timeout_seconds
        while time.time() < deadline:
            html = self._get(url)
            if not html:
                # If the last response was a hard block (403/429/503), don't bother polling.
                # Let the caller fall back to a different scraper strategy.
                if getattr(self, "_last_status", None) in (403, 429, 503):
                    log.info(
                        "slow_download blocked (status=%s); giving up early", self._last_status
                    )
                    return None
                time.sleep(8)
                continue
            cdns = self._all_cdns(html)
            if cdns:
                # If only one, no point in racing
                if len(cdns) == 1:
                    return DownloadHandle(url=cdns[0], headers={}, expected_filename=None)
                # Race them in parallel - first 200 wins
                winner = _run_async(_probe_slow_servers_async(cdns))
                if winner:
                    return DownloadHandle(url=winner, headers={}, expected_filename=None)
                # Fall through to the first one even if probes failed
                return DownloadHandle(url=cdns[0], headers={}, expected_filename=None)
            # Honor countdown if present
            soup = BeautifulSoup(html, "lxml")
            cd = soup.select_one(".js-partner-countdown")
            wait = 8
            if cd:
                with contextlib.suppress(ValueError):
                    wait = int(cd.get_text(strip=True)) + 3
            wait = max(1, min(wait, int(deadline - time.time())))
            log.info("slow_download wait %ds for %s", wait, url)
            time.sleep(wait)
        return None

    @staticmethod
    def _all_cdns(html: str) -> list[str]:
        """Phase 6s.2: extract every CDN-looking URL from a slow_download
        page so they can be probed in parallel by
        _probe_slow_servers_async."""
        soup = BeautifulSoup(html, "lxml")
        urls: list[str] = []
        for span in soup.select("span.bg-gray-200.break-all"):
            t = span.get_text(strip=True)
            if t.startswith("http") and url_has_book_ext(t):
                urls.append(t)
        for a in soup.find_all("a", href=True):
            if re.search(r"download\s*now|fast.*partner|slow.*partner", a.get_text(), re.I):
                href = a["href"]
                if href.startswith("http") and href not in urls:
                    urls.append(href)
        for m in ANNAS_CDN_REGEX.finditer(html):
            url = m.group(0).rstrip(".,;)\"'")
            if url_has_book_ext(url) and url not in urls:
                urls.append(url)
        return urls

    @staticmethod
    def _first_cdn(html: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        span = soup.select_one("span.bg-gray-200.break-all")
        if span:
            t = span.get_text(strip=True)
            if t.startswith("http") and url_has_book_ext(t):
                return t
        for a in soup.find_all("a", href=True):
            if re.search(r"download\s*now", a.get_text(), re.I):
                href = a["href"]
                if href.startswith("http"):
                    return href
        m = ANNAS_CDN_REGEX.search(html)
        if m:
            url = m.group(0).rstrip(".,;)\"'")
            if url_has_book_ext(url):
                return url
        return None


# I-NEW-3: _run_async now lives in endless_library.async_utils.
# Re-exported here for back-compat with any code importing it from this module.
from endless_library.async_utils import _run_async  # noqa: E402


async def _probe_slow_servers_async(urls: list[str], *, timeout: float = 15.0) -> str | None:
    """Phase 6s.2: race a set of slow-server CDN URLs in parallel.
    Returns the first URL that yields HTTP 200 with a non-trivial
    body. Greasyfork userscript #544083 pattern; 3-5x median
    latency drop vs sequential probing."""
    import asyncio

    if not urls:
        return None
    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": BIBLICHOR_USER_AGENT},
        follow_redirects=True,
    ) as client:

        async def probe(url: str) -> str | None:
            try:
                # HEAD first to avoid downloading the whole file
                r = await client.head(url)
                if r.status_code in (200, 206):
                    return url
                # Some CDNs reject HEAD; fall back to ranged GET
                r = await client.get(url, headers={"Range": "bytes=0-1023"})
                if r.status_code in (200, 206) and len(r.content) > 0:
                    return url
            except httpx.HTTPError:
                pass
            return None

        tasks = [asyncio.create_task(probe(u)) for u in urls]
        try:
            for coro in asyncio.as_completed(tasks):
                res = await coro
                if res:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    return res
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
    return None
