"""BDeBooks Bangla PDF scraper.

bdebooks.com is a WordPress-based catalog of Bangla PDFs.
Live search via ?s=<query>; each result carries a category that we
extract into Candidate.categories so the per-source excluded_categories
denylist can filter Islamic / religious content before pushing to
the queue.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from endless_library.config import BdebooksCfg
from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.http_client import BIBLICHOR_USER_AGENT as _BIBLICHOR_UA
from endless_library.scrapers.http_client import make_client

log = logging.getLogger(__name__)

BASE = "https://bdebooks.com"
USER_AGENT = f"Mozilla/5.0 (compatible; {_BIBLICHOR_UA}; +bdebooks scraper)"

# Cap the number of detail-page fetches per search so the bench's 20s per-query
# timeout is not exceeded by N*1-3s/page serial fetches (ultrareview D).
_MAX_DETAIL_FETCHES = 3


class BDeBooks:
    """Strategy entry: bdebooks."""

    name = "bdebooks"
    provider = "bdebooks"

    def __init__(self, cfg: BdebooksCfg, **kw) -> None:
        self._cfg = cfg

    # ---------------- Public API ----------------

    def search(self, query: SearchQuery) -> list[Candidate]:
        try:
            client = make_client(timeout=20)
            r = client.get(
                BASE,
                params={"s": query.title},
                headers={"User-Agent": USER_AGENT},
            )
        except Exception as e:
            log.warning("bdebooks: search failed: %s", e)
            return []
        if r.status_code != 200:
            return []

        soup = BeautifulSoup(r.text, "lxml")
        excluded = set(getattr(self._cfg, "excluded_categories", None) or ())
        out: list[Candidate] = []

        for art in soup.select("article.post"):
            title_el = (
                art.select_one("a.entry-title") or art.select_one("h2 a") or art.select_one("h1 a")
            )
            if not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            detail_url = title_el.get("href", "")
            if not detail_url:
                continue

            # Collect all category links from this post block
            categories = tuple(
                a.get_text(" ", strip=True)
                for a in art.select("a[href*='/category/'], a.category, a[rel='category tag']")
            )

            # Apply denylist before fetching detail page
            if excluded and set(categories) & excluded:
                log.debug(
                    "bdebooks: skipping %r — category %s in denylist",
                    title,
                    set(categories) & excluded,
                )
                continue

            # Cap serial detail fetches to avoid bench timeout (ultrareview D)
            if len(out) >= _MAX_DETAIL_FETCHES:
                break

            # Fetch detail page to extract the PDF download URL
            pdf_url = self._extract_pdf(detail_url, client)
            if not pdf_url:
                log.debug("bdebooks: no PDF found for %r at %s", title, detail_url)
                continue

            out.append(
                Candidate(
                    provider="bdebooks",
                    md5=None,
                    title=title,
                    author=None,
                    language="bn",
                    format="pdf",
                    filesize_bytes=None,
                    year=None,
                    publisher=None,
                    edition_hints="",
                    detail_url=detail_url,
                    categories=categories,
                    raw={"pdf_url": pdf_url},
                )
            )
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        pdf_url = (candidate.raw or {}).get("pdf_url")
        if not pdf_url:
            log.warning("bdebooks: candidate has no pdf_url in raw")
            return None
        return DownloadHandle(
            url=pdf_url,
            headers={"User-Agent": USER_AGENT},
            expected_filename=(candidate.title or "book").replace("/", "_") + ".pdf",
        )

    # ---------------- Internals ----------------

    def _extract_pdf(self, detail_url: str, client) -> str | None:
        try:
            r = client.get(detail_url, headers={"User-Agent": USER_AGENT})
        except Exception as e:
            log.warning("bdebooks: detail fetch %s failed: %s", detail_url, e)
            return None
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().endswith(".pdf"):
                return urljoin(detail_url, href)
        return None
