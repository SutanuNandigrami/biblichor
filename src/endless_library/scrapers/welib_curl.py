"""Welib scraper. Welib runs behind Cloudflare so we route through FlareSolverr
when configured. Welib has an /auto_download/<md5>/0/0 endpoint that resolves
to the CDN URL directly (no slow_download wait, unlike Anna's).

Search DOM uses the same /md5/<hash> link pattern as Anna's Archive — both
projects share a common fork.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.flaresolverr import FlareSolverr, FlareSolverrError
from endless_library.scrapers.base import parse_filesize
from endless_library.scrapers.rate_limit import TokenBucket

log = logging.getLogger(__name__)

WELIB_BASE = "https://welib.org"
WELIB_CDN_REGEX = re.compile(
    r'https?://[^"\'\s>]*?(?:welib-premium|welib-public|welib\.[a-z]+)\.org[^"\'\s>]*',
    re.IGNORECASE,
)


class WelibCurl:
    """Welib via FlareSolverr (forced; curl-cffi gets 403 on every welib endpoint
    because of Cloudflare). Name retained for backward-compat with config order.
    """

    name = "welib_curl"
    provider = "welib"

    def __init__(
        self,
        cfg: ScrapersCfg,
        *,
        http_get=None,  # legacy test hook (status, text)
        flaresolverr: FlareSolverr | None = None,
    ) -> None:
        self.cfg = cfg
        self.bucket = TokenBucket(capacity=6, period_seconds=60)
        self._http_get = http_get
        self._fs = flaresolverr

    def search(self, query: SearchQuery) -> list[Candidate]:
        q = f"{query.title} {query.author or ''}".strip()
        url = f"{WELIB_BASE}/search?q={quote_plus(q)}"
        html = self._get(url)
        if not html:
            return []
        return self._parse_results(html)

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        if not candidate.md5:
            return None
        # Welib's auto_download endpoint redirects straight to the CDN.
        # Going through FlareSolverr means Chromium follows the redirect; the
        # final solved URL is what we want.
        auto_url = f"{WELIB_BASE}/auto_download/{candidate.md5}/0/0"
        html, final_url = self._get_with_final_url(auto_url)
        if final_url and self._looks_like_cdn(final_url):
            return DownloadHandle(url=final_url, headers={}, expected_filename=None)
        if html:
            cdn = self._first_cdn(html)
            if cdn:
                return DownloadHandle(url=cdn, headers={}, expected_filename=None)
        # Fallback: scrape the /md5/ page for a download link
        md5_url = f"{WELIB_BASE}/md5/{candidate.md5}"
        html = self._get(md5_url)
        if not html:
            return None
        cdn = self._first_cdn(html)
        if cdn:
            return DownloadHandle(url=cdn, headers={}, expected_filename=None)
        return None

    # -------------------- HTTP transport --------------------

    def _get(self, url: str) -> str | None:
        html, _ = self._get_with_final_url(url)
        return html

    def _get_with_final_url(self, url: str) -> tuple[str | None, str | None]:
        sleep = self.bucket.acquire(url)
        if sleep > 0:
            time.sleep(sleep)
        if self._http_get is not None:
            status, text = self._http_get(url, headers={"User-Agent": "Mozilla/5.0"})
            return (text, url) if status == 200 else (None, None)
        # Lazy-init FlareSolverr from cfg
        fs = self._fs
        if fs is None:
            fs = FlareSolverr(self.cfg.flaresolverr_url, max_timeout_ms=60_000)
            self._fs = fs
        try:
            r = fs.get(url)
        except FlareSolverrError as e:
            log.warning("welib FlareSolverr error %s: %s", url, e)
            return None, None
        except Exception as e:
            log.warning("welib FlareSolverr request failed %s: %s", url, e)
            return None, None
        if r.status_code != 200:
            log.warning("welib status=%d for %s", r.status_code, url)
            return None, None
        # FlareSolverr's solution.url is the final URL after redirects (when
        # Chromium followed any). We only get the body, but the body itself
        # tells us a lot. For auto_download specifically, if the response is
        # short and references a CDN URL, that's our handle.
        return r.text, url

    # -------------------- parsing --------------------

    @staticmethod
    def _looks_like_cdn(url: str) -> bool:
        return bool(WELIB_CDN_REGEX.search(url))

    @staticmethod
    def _first_cdn(html: str) -> str | None:
        m = WELIB_CDN_REGEX.search(html)
        return m.group(0) if m else None

    @staticmethod
    def _parse_results(html: str) -> list[Candidate]:
        soup = BeautifulSoup(html, "lxml")
        out: list[Candidate] = []
        seen: set[str] = set()
        # Welib search rows have /md5/<hash> anchors (same pattern as Anna's).
        # Per inspection, the image anchor (empty text) appears first, then a
        # title anchor inside .book-title. The title anchor exposes the human title.
        for md5_a in soup.select('a[href^="/md5/"]'):
            href = md5_a.get("href", "")
            m = re.search(r"/md5/([a-f0-9]{32})", href)
            if not m:
                continue
            md5 = m.group(1)
            if md5 in seen:
                continue
            seen.add(md5)
            title = md5_a.get_text(" ", strip=True) or None
            # Walk up to find the result card and pull metadata text
            row = md5_a
            for _ in range(6):
                row = row.parent
                if row is None:
                    break
                cls = " ".join(row.get("class") or [])
                if "book-card" in cls or "border" in cls:
                    break
            row_text = row.get_text(" ", strip=True) if row else ""
            # If our anchor was the image (empty text), the title is usually in
            # a sibling .book-title anchor under the same parent.
            if not title and row is not None:
                tnode = row.select_one(".book-title, .js-vim-focus")
                if tnode:
                    title = tnode.get_text(" ", strip=True)
            fmt = WelibCurl._parse_format(row_text)
            filesize = parse_filesize(row_text)
            year = WelibCurl._parse_year(row_text)
            language = WelibCurl._parse_language(row_text)
            out.append(
                Candidate(
                    provider="welib",
                    md5=md5,
                    title=title,
                    author=None,
                    language=language,
                    format=fmt,
                    filesize_bytes=filesize,
                    year=year,
                    publisher=None,
                    edition_hints=row_text.lower()[:400],
                    detail_url=urljoin(WELIB_BASE, href),
                    raw={"row_text": row_text[:400]},
                )
            )
            if len(out) >= 25:
                break
        return out

    @staticmethod
    def _parse_format(s: str) -> str | None:
        m = re.search(
            r"\b(epub|pdf|mobi|azw3|djvu|fb2|cbz|cbr|doc|docx|rtf|txt|lit)\b",
            s,
            re.I,
        )
        return m.group(1).lower() if m else None

    @staticmethod
    def _parse_year(s: str) -> int | None:
        m = re.search(r"\b(19|20)\d{2}\b", s)
        return int(m.group(0)) if m else None

    @staticmethod
    def _parse_language(s: str) -> str | None:
        m = re.search(
            r"\b(English|German|French|Spanish|Italian|Russian|Portuguese|Hindi|Bengali|Chinese|Japanese)\b",
            s,
            re.I,
        )
        if not m:
            return None
        mapping = {
            "english": "en",
            "german": "de",
            "french": "fr",
            "spanish": "es",
            "italian": "it",
            "russian": "ru",
            "portuguese": "pt",
            "hindi": "hi",
            "bengali": "bn",
            "chinese": "zh",
            "japanese": "ja",
        }
        return mapping.get(m.group(1).lower())
