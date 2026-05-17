"""Goodreads Listopia (`/list/show/N.Name`) — public list scraping (HTML, no API)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

import httpx
from bs4 import BeautifulSoup

from endless_library.domain.models import BookRef

log = logging.getLogger(__name__)

BASE = "https://www.goodreads.com/list/show/"


class GoodreadsListopia:
    """Identifier: list slug or full `/list/show/N.Name` URL (mins to slug)."""

    name = "goodreads_listopia"

    def __init__(self, *, http_timeout: float = 30.0, fetch: callable | None = None) -> None:
        self._timeout = http_timeout
        self._fetch = fetch

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        url = self._resolve_url(identifier)
        soup = BeautifulSoup(self._get(url), "lxml")
        rows = soup.select('tr[itemtype="http://schema.org/Book"]')
        if not rows:
            # Goodreads sometimes wraps differently; fall back to title anchors
            rows = soup.select("table.tableList tr")
        out: list[BookRef] = []
        for row in rows:
            title_el = row.select_one('a.bookTitle, span[itemprop="name"]')
            author_el = row.select_one('a.authorName, span[itemprop="author"]')
            if not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            if " (" in title:
                title = title.split(" (", 1)[0]
            author = author_el.get_text(" ", strip=True) if author_el else None
            # Stable ID: pull book id from the href
            link = title_el.get("href", "") or ""
            m = re.search(r"/book/show/(\d+)", link)
            source_id = m.group(1) if m else f"listopia:{identifier}:{title.lower()}"
            out.append(
                BookRef(
                    title=title,
                    author=author,
                    isbn13=None,
                    source="manual",  # listopia rolls up as a source variant of manual
                    source_id=f"listopia:{source_id}",
                )
            )
        return out

    @staticmethod
    def _resolve_url(identifier: str) -> str:
        ident = identifier.strip()
        if ident.startswith("http"):
            return ident
        if ident.startswith("/list/show/"):
            return "https://www.goodreads.com" + ident
        return BASE + ident.lstrip("/")

    def _get(self, url: str) -> str:
        if self._fetch is not None:
            return self._fetch(url)
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "endless-library/0.1"},
        ) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.text
