from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.base import parse_filesize
from endless_library.scrapers.rate_limit import TokenBucket

log = logging.getLogger(__name__)

WELIB_BASE = "https://welib.org"
WELIB_CDN_HOSTS = ("welib-premium.org", "welib-public.org")
WELIB_CDN_REGEX = re.compile(
    r'https?://[^"\'\s>]*?(?:welib-premium|welib-public)\.org[^"\'\s>]*', re.IGNORECASE
)


class WelibCurl:
    name = "welib_curl"
    provider = "welib"

    def __init__(self, cfg: ScrapersCfg, *, http_get=None) -> None:
        self.cfg = cfg
        self.bucket = TokenBucket(capacity=6, period_seconds=60)
        self._http_get = http_get

    def search(self, query: SearchQuery) -> list[Candidate]:
        q = f"{query.title} {query.author or ''}".strip()
        url = f"{WELIB_BASE}/search?q={quote_plus(q)}"
        html = self._get(url)
        if not html:
            return []
        return self._parse_results(html)

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        # Welib detail pages can carry CDN URL directly or behind a fast_view/download link
        html = self._get(candidate.detail_url)
        if not html:
            return None
        cdn = self._first_cdn(html)
        if cdn:
            return DownloadHandle(url=cdn, headers={}, expected_filename=None)
        # Try the action links (fast_view / fast_preview / /download/) — userscript pattern
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select(
            'a[href*="fast_view"], a[href*="fast_preview"], a[href*="/download/"]'
        ):
            action_url = urljoin(candidate.detail_url, a["href"])
            inner = self._get(action_url)
            if not inner:
                continue
            cdn = self._first_cdn(inner)
            if cdn:
                return DownloadHandle(url=cdn, headers={}, expected_filename=None)
        return None

    def _get(self, url: str) -> str | None:
        import time

        sleep = self.bucket.acquire(url)
        if sleep > 0:
            time.sleep(sleep)
        if self._http_get is not None:
            status, text = self._http_get(url, headers={"User-Agent": "Mozilla/5.0"})
            return text if status == 200 else None
        from curl_cffi import requests as cf

        try:
            r = cf.get(
                url, impersonate="chrome120", timeout=30, headers={"User-Agent": "Mozilla/5.0"}
            )
            return r.text if r.status_code == 200 else None
        except Exception as e:
            log.warning("welib curl error %s: %s", url, e)
            return None

    @staticmethod
    def _first_cdn(html: str) -> str | None:
        m = WELIB_CDN_REGEX.search(html)
        if m:
            return m.group(0)
        return None

    @staticmethod
    def _parse_results(html: str) -> list[Candidate]:
        soup = BeautifulSoup(html, "lxml")
        out: list[Candidate] = []
        # Welib search rows have anchors with a hash-based path
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if not re.match(r"^/[a-f0-9]{8,}\b", href, re.I):
                continue
            title = a.get_text(" ", strip=True) or None
            block = a.parent.get_text(" ", strip=True) if a.parent else ""
            fmt_match = re.search(r"\b(epub|pdf|mobi|azw3|djvu|fb2)\b", block, re.I)
            fmt = fmt_match.group(1).lower() if fmt_match else None
            out.append(
                Candidate(
                    provider="welib",
                    md5=None,
                    title=title,
                    author=None,
                    language=None,
                    format=fmt,
                    filesize_bytes=parse_filesize(block),
                    year=None,
                    publisher=None,
                    edition_hints=block.lower()[:300],
                    detail_url=urljoin(WELIB_BASE, href),
                    raw={"block_text": block[:400]},
                )
            )
            if len(out) >= 25:
                break
        return out
