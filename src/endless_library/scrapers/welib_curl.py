"""Welib scraper.

Architecture:
  * search uses FlareSolverr to clear Cloudflare on welib.org
  * for resolve_cdn we hit the /md5/<hash> page (already past CF cookie via FS)
    and prefer in this order:
      1. IPFS gateway URLs (no CF, no countdown, direct file)
      2. /slow_download/<md5>/0/0 polled for the CDN URL (same flow as Anna's)
      3. CDN URLs embedded on the md5 page itself
  * we also accept a meta-refresh fallback (inspired by the userscript) — some
    welib pages auto-redirect to the file via <meta http-equiv="refresh">.
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from endless_library.config import ScrapersCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.flaresolverr import FlareSolverr, FlareSolverrError
from endless_library.scrapers.base import BOOK_EXTENSIONS, parse_filesize
from endless_library.scrapers.rate_limit import TokenBucket

log = logging.getLogger(__name__)

WELIB_BASE = "https://welib.org"

# Anything that looks like a publicly-fetchable book payload.
_IPFS_RE = re.compile(
    r'https?://[^"\'\s<>]*?(?:ipfs(?:[.-][a-z0-9-]+)*\.[a-z]+|ipfs\.io)'
    r'/ipfs/[a-z0-9]+[^"\'\s<>]*',
    re.IGNORECASE,
)
_WELIB_CDN_RE = re.compile(
    r'https?://[^"\'\s<>]*?welib-(?:premium|public)\.org[^"\'\s<>]*',
    re.IGNORECASE,
)
_ANNAS_CDN_RE = re.compile(
    r'https://[^/\s"\']+/d3/y/[^/\s"\']+/[^"\'\s>]+',
    re.IGNORECASE,
)
_META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+url=["\']?(https?://[^"\'>\s]+)',
    re.IGNORECASE,
)


def _is_book_payload_url(url: str) -> bool:
    """True if URL plausibly points at a book file (not a cover/thumbnail)."""
    lc = url.lower()
    if "/covers/" in lc or "covers/proxy" in lc:
        return False
    path = lc.split("?", 1)[0]
    if any(path.endswith("." + ext) for ext in BOOK_EXTENSIONS):
        return True
    # IPFS URLs typically lack a normal extension but carry filename=...epub
    if "/ipfs/" in path:
        # require either book ext somewhere in the query or filename=
        if any(("." + ext) in lc for ext in BOOK_EXTENSIONS):
            return True
        if "filename=" in lc:
            return True
    return False


class WelibCurl:
    """Welib scraper (FlareSolverr-backed). Class name kept for config back-compat."""

    name = "welib_curl"
    provider = "welib"

    def __init__(
        self,
        cfg: ScrapersCfg,
        *,
        http_get=None,
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
        # Hit the md5 page (same as /auto_download/ landing for welib).
        md5_url = f"{WELIB_BASE}/md5/{candidate.md5}"
        html = self._get(md5_url)
        if not html:
            return None
        # 1) Prefer IPFS — no CF, no countdown — but HEAD-verify; many public
        #    gateways return 410 for copyrighted CIDs.
        for m in _IPFS_RE.finditer(html):
            url = m.group(0)
            if not _is_book_payload_url(url):
                continue
            if self._ipfs_reachable(url):
                log.info("welib: picked IPFS URL (HEAD ok)")
                return DownloadHandle(url=url, headers={}, expected_filename=None)
            log.info("welib: IPFS URL not reachable, will fall back")
            break  # all gateway variants in the page share the same CID — same outcome
        # 2) Meta refresh
        m = _META_REFRESH_RE.search(html)
        if m and _is_book_payload_url(m.group(1)):
            log.info("welib: picked meta-refresh URL")
            return DownloadHandle(url=m.group(1), headers={}, expected_filename=None)
        # 3) welib-(premium|public).org URL that's not a cover
        for cm in _WELIB_CDN_RE.finditer(html):
            url = cm.group(0)
            if _is_book_payload_url(url):
                log.info("welib: picked welib-CDN URL")
                return DownloadHandle(url=url, headers={}, expected_filename=None)
        # 4) /slow_download/<md5>/0/0 — polling flow (same as Anna's)
        slow_url = f"{WELIB_BASE}/slow_download/{candidate.md5}/0/0"
        return self._poll_slow_download(slow_url)

    # ---------------- transport ----------------

    def _get(self, url: str) -> str | None:
        sleep = self.bucket.acquire(url)
        if sleep > 0:
            time.sleep(sleep)
        if self._http_get is not None:
            status, text = self._http_get(url, headers={"User-Agent": "Mozilla/5.0"})
            return text if status == 200 else None
        fs = self._fs
        if fs is None:
            fs = FlareSolverr(self.cfg.flaresolverr_url, max_timeout_ms=60_000)
            self._fs = fs
        try:
            r = fs.get(url)
        except FlareSolverrError as e:
            log.warning("welib FS error %s: %s", url, e)
            return None
        except Exception as e:
            log.warning("welib FS request failed %s: %s", url, e)
            return None
        if r.status_code != 200:
            log.warning("welib status=%d for %s", r.status_code, url)
            return None
        return r.text

    def _ipfs_reachable(self, url: str, *, timeout: float = 8.0) -> bool:
        """HEAD-probe an IPFS URL. Returns True only on 2xx response."""
        import httpx

        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                verify=False,  # public IPFS gateways often have cert issues
                headers={"User-Agent": "endless-library/0.1"},
            ) as c:
                r = c.head(url)
                return 200 <= r.status_code < 300
        except Exception as e:
            log.info("welib IPFS HEAD failed for %s: %s", url[:80], e)
            return False

    def _poll_slow_download(self, url: str) -> DownloadHandle | None:
        deadline = time.time() + self.cfg.slow_download_timeout_seconds
        while time.time() < deadline:
            html = self._get(url)
            if not html:
                time.sleep(8)
                continue
            for rx in (_ANNAS_CDN_RE, _IPFS_RE, _WELIB_CDN_RE):
                m = rx.search(html)
                if m and _is_book_payload_url(m.group(0)):
                    return DownloadHandle(url=m.group(0), headers={}, expected_filename=None)
            soup = BeautifulSoup(html, "lxml")
            cd = soup.select_one(".js-partner-countdown")
            wait = 8
            if cd:
                with contextlib.suppress(ValueError):
                    wait = int(cd.get_text(strip=True)) + 3
            wait = max(1, min(wait, int(deadline - time.time())))
            log.info("welib slow_download wait %ds", wait)
            time.sleep(wait)
        return None

    # ---------------- parsing ----------------

    @staticmethod
    def _parse_results(html: str) -> list[Candidate]:
        soup = BeautifulSoup(html, "lxml")
        out: list[Candidate] = []
        seen: set[str] = set()
        # The title anchors are /md5/<hash> with non-empty text.
        # Image anchors share the same href but have empty text — skip them.
        # Build {md5: anchors[]} so we can require paired (image + title) anchors.
        # Sidebar recommendations have only one anchor for their md5.
        from collections import defaultdict

        groups: defaultdict[str, list] = defaultdict(list)
        for a in soup.select('a[href^="/md5/"]'):
            m = re.search(r"/md5/([a-f0-9]{32})", a.get("href", ""))
            if m:
                groups[m.group(1)].append(a)
        for md5, anchors in groups.items():
            if len(anchors) < 2:
                continue  # likely a sidebar/recommendation single-link
            # Find the one with non-empty text (the title)
            title_a = next((x for x in anchors if x.get_text(" ", strip=True)), None)
            if title_a is None:
                continue
            a = title_a
            text = a.get_text(" ", strip=True)
            if md5 in seen:
                continue
            seen.add(md5)
            # Walk up to the row container
            row = a
            for _ in range(8):
                row = row.parent
                if row is None:
                    break
                cls = " ".join(row.get("class") or [])
                if "book-list" in cls or "flex-wrap" in cls:
                    break
            row_text = row.get_text(" ", strip=True) if row else text
            out.append(
                Candidate(
                    provider="welib",
                    md5=md5,
                    title=text,
                    author=None,
                    language=WelibCurl._lang(row_text),
                    format=WelibCurl._fmt(row_text),
                    filesize_bytes=parse_filesize(row_text),
                    year=WelibCurl._year(row_text),
                    publisher=None,
                    edition_hints=row_text.lower()[:400],
                    detail_url=urljoin(WELIB_BASE, a["href"]),
                    raw={"row_text": row_text[:400]},
                )
            )
            if len(out) >= 25:
                break
        return out

    @staticmethod
    def _fmt(s: str) -> str | None:
        m = re.search(
            r"\b(epub|pdf|mobi|azw3|djvu|fb2|cbz|cbr|doc|docx|rtf|txt|lit)\b",
            s,
            re.I,
        )
        return m.group(1).lower() if m else None

    @staticmethod
    def _year(s: str) -> int | None:
        m = re.search(r"\b(19|20)\d{2}\b", s)
        return int(m.group(0)) if m else None

    @staticmethod
    def _lang(s: str) -> str | None:
        m = re.search(
            r"\b(English|German|French|Spanish|Italian|Russian|Portuguese|Hindi|Bengali|Chinese|Japanese)\b",
            s,
            re.I,
        )
        if not m:
            return None
        mp = {
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
        return mp.get(m.group(1).lower())
