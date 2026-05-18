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

LIBGEN_MIRRORS = (
    # Live mirrors per shadowlibraries.github.io/DirectDownloads/libgen/
    # Verified reachable 2026-05-18. The .is/.rs/.st mirrors have been flaky
    # for a year — dropped from the rotation.
    "https://libgen.li",
    "https://libgen.gl",
    "https://libgen.la",
    "https://libgen.vg",
    "https://libgen.bz",
)


class LibgenCurl:
    name = "libgen_curl"
    provider = "libgen"

    def __init__(self, cfg: ScrapersCfg, *, http_get=None) -> None:
        self.cfg = cfg
        self.bucket = TokenBucket(capacity=6, period_seconds=60)
        self._http_get = http_get
        # Use cfg.annas_mirrors if it has libgen entries, else hard-coded list
        self._mirrors = list(LIBGEN_MIRRORS)
        self._mirror_idx = 0
        # Helper accessor
        # (kept simple; mirror selection happens in search() and resolve_cdn())

    def search(self, query: SearchQuery) -> list[Candidate]:
        q = f"{query.title} {query.author or ''}".strip()
        url = (
            f"{self._mirrors[self._mirror_idx]}/index.php?req={quote_plus(q)}"
            "&lg_topic=libgen&open=0&view=simple&res=25&phrase=1&column=def"
        )
        html = self._get(url)
        if not html:
            return []
        return self._parse_results(html, self._mirrors[self._mirror_idx])

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        html = self._get(candidate.detail_url)
        if not html:
            # All direct-page paths failed; try IPFS gateway from libgen.is
            ipfs = self._try_ipfs_fallback(candidate.md5 or "")
            if ipfs:
                return DownloadHandle(url=ipfs, headers={}, expected_filename=None)
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

    def _try_ipfs_fallback(self, md5: str) -> str | None:
        """libgen.is exposes /ads.php?md5=... which usually offers an IPFS gateway URL."""
        if not md5:
            return None
        for base in self._mirrors:
            url = f"{base}/ads.php?md5={md5}"
            html = self._get(url)
            if not html:
                continue
            for m in re.finditer(r'(https?://[^"\'\s<>]+?/ipfs/[^"\'\s<>]+)', html, re.I):
                return m.group(0)
        return None

    def _parse_results(self, html: str, base_url: str) -> list[Candidate]:
        """libgen.li simple view results table: td[0] title, td[1] author,
        td[2] publisher, td[3] year, td[4] lang, td[6] size, td[7] format,
        td[8] mirror anchors (ads.php?md5=...)."""
        soup = BeautifulSoup(html, "lxml")
        out: list[Candidate] = []
        for tr in soup.select("tr"):
            cells = tr.select("td")
            if len(cells) < 9:
                continue
            md5_anchor = None
            for a in cells[8].select("a[href]"):
                if "ads.php?md5=" in a.get("href", ""):
                    md5_anchor = a
                    break
            if md5_anchor is None:
                continue
            m = re.search(r"md5=([a-f0-9]{32})", md5_anchor.get("href", ""))
            if not m:
                continue
            md5 = m.group(1)
            title = cells[0].get_text(" ", strip=True) or None
            author = cells[1].get_text(" ", strip=True) or None
            publisher = cells[2].get_text(" ", strip=True) or None
            year_text = cells[3].get_text(" ", strip=True)
            year = None
            if year_text.isdigit():
                year = int(year_text)
            lang_text = cells[4].get_text(" ", strip=True).lower()
            lang_map = {
                "english": "en",
                "german": "de",
                "french": "fr",
                "spanish": "es",
                "italian": "it",
                "russian": "ru",
                "portuguese": "pt",
                "hindi": "hi",
            }
            language = lang_map.get(lang_text)
            size_text = cells[6].get_text(" ", strip=True)
            filesize = parse_filesize(size_text)
            fmt = (cells[7].get_text(" ", strip=True) or "").lower()
            if fmt not in {"epub", "pdf", "mobi", "azw3", "djvu", "fb2"}:
                fmt = None
            ads_href = md5_anchor.get("href", "")
            row_text = tr.get_text(" ", strip=True)
            out.append(
                Candidate(
                    provider="libgen",
                    md5=md5,
                    title=title,
                    author=author,
                    language=language,
                    format=fmt,
                    filesize_bytes=filesize,
                    year=year,
                    publisher=publisher,
                    edition_hints=row_text.lower()[:300],
                    detail_url=urljoin(base_url, ads_href),
                )
            )
            if len(out) >= 25:
                break
        return out
