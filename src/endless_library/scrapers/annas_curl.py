from __future__ import annotations

import contextlib
import logging
import random
import re
import time
from typing import Any
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.base import ANNAS_CDN_REGEX, parse_filesize, url_has_book_ext
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
            params = {
                "q": f"{query.title} {query.author or ''}".strip(),
                "ext": fmt,
                "lang": query.language,
                "sort": "",
            }
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
        return self._poll_slow_download(slow_url)

    # ----- Internals -----

    def _get(self, url: str) -> str | None:
        sleep = self.bucket.acquire(url)
        if sleep > 0:
            log.debug("rate-limit sleep %.1fs for %s", sleep, url)
            time.sleep(sleep)
        else:
            time.sleep(self.cfg.request_delay_seconds + random.uniform(0.5, 2.0))
        if self._http_get is not None:
            status, text = self._http_get(url, headers=DEFAULT_HEADERS)
        else:
            from curl_cffi import requests as cf

            try:
                r = cf.get(url, headers=DEFAULT_HEADERS, impersonate="chrome120", timeout=30)
                status, text = r.status_code, r.text
            except Exception as e:
                log.warning("curl_cffi failed for %s: %s", url, e)
                self._last_status = None
                return None
        self._last_status = status
        if status == 200:
            return text
        log.warning("HTTP %d for %s", status, url)
        if status in (403, 429, 503):
            self.mirrors.next_after_failure()
        return None

    def _parse_search_results(self, html: str, fmt_hint: str) -> list[Candidate]:
        soup = BeautifulSoup(html, "lxml")
        out: list[Candidate] = []
        seen: set[str] = set()
        # `js-vim-focus` is Anna's per-result title anchor; one per row, no sidebar dupes.
        for a in soup.select('a.js-vim-focus[href^="/md5/"]'):
            href = a.get("href", "")
            m = re.search(r"/md5/([a-f0-9]{32})", href)
            if not m:
                continue
            md5 = m.group(1)
            if md5 in seen:
                continue
            seen.add(md5)
            title = a.get_text(" ", strip=True) or None
            # The row container: nearest ancestor with class containing "border-b"
            row = a
            for _ in range(6):
                row = row.parent
                if row is None:
                    break
                if row.name == "div" and "border-b" in (row.get("class") or []):
                    break
            row_text = row.get_text(" ", strip=True) if row else ""
            # Look for a filename-hint div above the title (contains "/<md5>.<ext>")
            fmt = None
            for sib in a.parent.children if a.parent else []:
                sib_text = (
                    getattr(sib, "get_text", lambda **_: "")(" ", strip=True)
                    if hasattr(sib, "get_text")
                    else ""
                )
                fm = re.search(
                    r"\.(epub|pdf|mobi|azw3|djvu|fb2|cbz|cbr|doc|docx|rtf|txt|lit)\b",
                    sib_text,
                    re.I,
                )
                if fm:
                    fmt = fm.group(1).lower()
                    break
            if not fmt:
                fmt = self._parse_format(row_text) or fmt_hint
            filesize = parse_filesize(row_text)
            language = self._parse_language(row_text)
            year = self._parse_year(row_text)
            edition_hints = row_text.lower()[:400]
            out.append(
                Candidate(
                    provider="annas",
                    md5=md5,
                    title=title,
                    author=None,
                    language=language,
                    format=fmt,
                    filesize_bytes=filesize,
                    year=year,
                    publisher=None,
                    edition_hints=edition_hints,
                    detail_url=urljoin(self.mirrors.current, href),
                    raw={"row_text": row_text[:400]},
                )
            )
            if len(out) >= 25:
                break
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
            cdn = self._first_cdn(html)
            if cdn:
                return DownloadHandle(url=cdn, headers={}, expected_filename=None)
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
