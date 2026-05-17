from __future__ import annotations

import logging
import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.base import parse_filesize, url_has_book_ext
from endless_library.scrapers.rate_limit import TokenBucket

log = logging.getLogger(__name__)

LIBGEN_BASE = "https://libgen.li"


class LibgenCurl:
    name = "libgen_curl"
    provider = "libgen"

    def __init__(self, cfg: ScrapersCfg, *, http_get=None) -> None:
        self.cfg = cfg
        self.bucket = TokenBucket(capacity=6, period_seconds=60)
        self._http_get = http_get

    def search(self, query: SearchQuery) -> list[Candidate]:
        q = f"{query.title} {query.author or ''}".strip()
        url = (
            f"{LIBGEN_BASE}/index.php?req={quote_plus(q)}"
            "&lg_topic=libgen&open=0&view=simple&res=25&phrase=1&column=def"
        )
        html = self._get(url)
        if not html:
            return []
        return self._parse_results(html)

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        html = self._get(candidate.detail_url)
        if not html:
            return None
        soup = BeautifulSoup(html, "lxml")
        # libgen.li exposes "GET" or direct ads links
        for a in soup.select("a[href]"):
            href = a["href"]
            full = urljoin(candidate.detail_url, href)
            if url_has_book_ext(full):
                return DownloadHandle(url=full, headers={}, expected_filename=None)
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
            log.warning("libgen curl error %s: %s", url, e)
            return None

    @staticmethod
    def _parse_results(html: str) -> list[Candidate]:
        soup = BeautifulSoup(html, "lxml")
        out: list[Candidate] = []
        for tr in soup.select("tr"):
            cells = tr.select("td")
            if len(cells) < 4:
                continue
            link = tr.select_one('a[href*="ads.php"], a[href*="/book/"]')
            if not link:
                continue
            href = link.get("href", "")
            title = link.get_text(" ", strip=True) or None
            block = tr.get_text(" ", strip=True)
            fmt_match = re.search(r"\b(epub|pdf|mobi|azw3|djvu|fb2)\b", block, re.I)
            fmt = fmt_match.group(1).lower() if fmt_match else None
            out.append(
                Candidate(
                    provider="libgen",
                    md5=None,
                    title=title,
                    author=None,
                    language=None,
                    format=fmt,
                    filesize_bytes=parse_filesize(block),
                    year=None,
                    publisher=None,
                    edition_hints=block.lower()[:300],
                    detail_url=urljoin(LIBGEN_BASE, href),
                )
            )
            if len(out) >= 25:
                break
        return out
