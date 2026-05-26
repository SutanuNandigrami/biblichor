"""kindlebangla.com — bulk catalog Source.

Walks every category index on kindlebangla.com (32 categories at the time
of writing) and yields a BookRef per book card. Idempotent: re-yields
known books each poll; dedup happens via the books table's
UNIQUE(source, source_id) constraint, so subsequent inserts are cheap.

Design notes
------------
- The site is paginated per category (~24 books/page, up to ~18 pages
  for the largest categories). We walk every page on every poll because
  there is no reliable "new arrivals only" feed; relying on insert dedup
  is cheap and matches how Goodreads-shelf polling works.
- 500 ms delay between requests; respect the site.
- Hard `max_pages` cap (default 500 pages ~= 12k books) so a single poll
  cannot run forever if the site adds new categories.
- The yielded BookRef carries `source="kindlebangla"` and
  `source_id=<bengali-slug>`. The pipeline (kindlebangla branch) routes
  these straight to the `kindlebangla_curl` scraper which already knows
  how to resolve `/download/<slug>` → Google Drive → EPUB.

The companion `kindlebangla_curl` scraper handles the per-book download
chain — this Source only fills the queue.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from typing import cast

import httpx
from bs4 import BeautifulSoup

from endless_library.domain.models import BookRef
from endless_library.scrapers.http_client import BIBLICHOR_USER_AGENT as _BIBLICHOR_UA

log = logging.getLogger(__name__)

BASE = "https://www.kindlebangla.com"
USER_AGENT = f"Mozilla/5.0 (compatible; {_BIBLICHOR_UA}; +kindlebangla source)"
REQ_DELAY_SEC = 0.5
DEFAULT_MAX_PAGES = 500


class KindleBangla:
    """Identifier: "full" (default — whole catalog) or "category:<slug>"."""

    name = "kindlebangla"

    def __init__(
        self,
        *,
        http_timeout: float = 30.0,
        fetch: Callable[[str], str] | None = None,
        delay_sec: float = REQ_DELAY_SEC,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        self._timeout = http_timeout
        self._fetch = fetch
        self._delay = delay_sec
        self._max_pages = max_pages

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        ident = (identifier or "full").strip()
        if ident.startswith("category:"):
            cat_slugs = [ident.split(":", 1)[1].strip()]
        else:
            cat_slugs = self._list_categories()

        pages_walked = 0
        seen: set[str] = set()
        for cat_slug in cat_slugs:
            page = 1
            while pages_walked < self._max_pages:
                html = self._get(f"{BASE}/category/{cat_slug}?page={page}")
                if html is None:
                    break
                cards = list(_extract_book_cards(html))
                if not cards:
                    break
                for ref in cards:
                    if ref.source_id in seen:
                        continue
                    seen.add(ref.source_id)
                    yield ref
                pages_walked += 1
                soup = BeautifulSoup(html, "html.parser")
                if not _has_next_page(soup, page):
                    break
                page += 1

    def _list_categories(self) -> list[str]:
        html = self._get(f"{BASE}/categories")
        if html is None:
            return []
        soup = BeautifulSoup(html, "html.parser")
        slugs: list[str] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("category/"):
                slugs.append(href.split("/", 1)[1])
            elif href.startswith("/category/"):
                slugs.append(href.split("/", 2)[2])
        # de-dup, preserve insertion order
        out: list[str] = []
        seen: set[str] = set()
        for s in slugs:
            s = s.split("?")[0].split("#")[0]
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    def _get(self, url: str) -> str | None:
        if self._fetch is not None:
            return self._fetch(url)
        try:
            time.sleep(self._delay)
            r = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
                follow_redirects=True,
            )
            if r.status_code != 200:
                log.warning("kindlebangla: %s -> HTTP %d", url, r.status_code)
                return None
            return r.text
        except httpx.HTTPError as e:
            log.warning("kindlebangla: GET %s failed: %s", url, e)
            return None


def _extract_book_cards(html: str) -> Iterable[BookRef]:
    """Yield BookRef per /book/<slug> card on a category page."""
    soup = BeautifulSoup(html, "html.parser")
    seen_local: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = cast(str, a["href"])
        if not (href.startswith("/book/") or href.startswith("book/")):
            continue
        slug = href.split("/book/", 1)[-1].split("?")[0].split("#")[0]
        if not slug or slug in seen_local:
            continue
        seen_local.add(slug)

        # Title: <h3> inside the card; author: <p>; cover alt also has title
        title = ""
        author: str | None = None
        h3 = a.find("h3") or (a.parent.find("h3") if a.parent else None)
        if h3 and h3.get_text(strip=True):
            title = h3.get_text(strip=True)
        else:
            img = a.find("img", alt=True)
            if img:
                title = (img["alt"] or "").strip()
        if not title:
            title = slug.replace("-", " ")

        p = a.find("p") or (a.parent.find("p") if a.parent else None)
        if p and p.get_text(strip=True):
            author = p.get_text(strip=True)

        yield BookRef(
            title=title,
            author=author,
            isbn13=None,
            source="kindlebangla",
            source_id=slug,
            source_added_at=None,
        )


def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    """True iff a `?page=<current+1>` link exists in the pagination block."""
    next_target = f"page={current_page + 1}"
    return any(next_target in a["href"] for a in soup.find_all("a", href=True))
