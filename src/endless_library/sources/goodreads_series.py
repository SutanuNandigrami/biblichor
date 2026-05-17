"""Goodreads Series (`/series/N-name`) — fetch all books in a series."""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterable

import httpx
from bs4 import BeautifulSoup

from endless_library.domain.models import BookRef

log = logging.getLogger(__name__)

BASE = "https://www.goodreads.com/series/"


class GoodreadsSeries:
    name = "goodreads_series"

    def __init__(self, *, http_timeout: float = 30.0, fetch: callable | None = None) -> None:
        self._timeout = http_timeout
        self._fetch = fetch

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        url = self._resolve_url(identifier)
        soup = BeautifulSoup(self._get(url), "lxml")
        out: list[BookRef] = []
        # Each series book is in div.listWithDividers__item; the heading
        # "Book N" indicates main-series ordering
        for row in soup.select("div.listWithDividers__item"):
            heading = row.find("h3")
            book_num: float | None = None
            if heading:
                m = re.search(r"Book\s+([0-9.]+)", heading.get_text(" ", strip=True), re.I)
                if m:
                    with contextlib.suppress(ValueError):
                        book_num = float(m.group(1))
            # Main series only — skip companions, novellas (non-integer book nums)
            if book_num is None or book_num <= 0 or (book_num % 1 != 0):
                continue
            title_el = row.select_one('a[itemprop="url"], span[itemprop="name"]')
            author_el = row.select_one('span[itemprop="author"]')
            if not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            if " (" in title:
                title = title.split(" (", 1)[0]
            author = author_el.get_text(" ", strip=True) if author_el else None
            link = title_el.get("href", "") if hasattr(title_el, "get") else ""
            m = re.search(r"/book/show/(\d+)", link or "")
            source_id = m.group(1) if m else f"series:{identifier}:{int(book_num)}"
            out.append(
                BookRef(
                    title=title,
                    author=author,
                    isbn13=None,
                    source="manual",
                    source_id=f"series:{source_id}",
                )
            )
        return out

    @staticmethod
    def _resolve_url(identifier: str) -> str:
        ident = identifier.strip()
        if ident.startswith("http"):
            return ident
        if ident.startswith("/series/"):
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
