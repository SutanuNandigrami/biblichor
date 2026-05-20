"""StoryGraph public-profile reading-list source (Phase 6s.3).

Identifier format: <username> (their public profile slug).
Token: none (public profiles only).
Polls daily.

Scrapes /to-read/<username> and /currently-reading/<username>.
ISBN-13 is not always present in StoryGraph; the pipeline can
backfill via metadata.openlibrary (Phase 6s.4) on intake.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx
from bs4 import BeautifulSoup

from endless_library.domain.models import BookRef

log = logging.getLogger(__name__)

BASE = "https://app.thestorygraph.com"


class StoryGraph:
    name = "storygraph"

    def __init__(self, *, http_timeout: float = 15.0) -> None:
        self._timeout = http_timeout

    def _fetch_shelf(self, username: str, shelf: str) -> str:
        url = f"{BASE}/{shelf}/{username}"
        try:
            r = httpx.get(
                url,
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": "endless-library/0.1"},
            )
            if r.status_code != 200:
                return ""
            return r.text
        except httpx.HTTPError as e:
            log.info("storygraph %s: %s", shelf, e)
            return ""

    def _parse_shelf(self, html: str, username: str, shelf: str) -> list[BookRef]:
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        out: list[BookRef] = []
        # Each book on a shelf is a div.book-title-author-and-series or similar
        # Use the most reliable selector that works against the public profile
        # template (May 2026): h3 a[href^="/books/"] is the title link.
        seen: set[str] = set()
        for a in soup.select('h3 a[href^="/books/"]'):
            slug = a.get("href", "").rsplit("/", 1)[-1]
            if not slug or slug in seen:
                continue
            seen.add(slug)
            title = a.get_text(" ", strip=True)
            # Author hangs next to the title; the StoryGraph template
            # uses a paragraph following the h3
            container = a.find_parent()
            author = None
            if container:
                p_author = container.find_next("p")
                if p_author:
                    author = p_author.get_text(" ", strip=True) or None
            out.append(
                BookRef(
                    title=title,
                    author=author,
                    isbn13=None,
                    source="storygraph",
                    source_id=f"storygraph:{username}:{shelf}:{slug}",
                )
            )
        return out

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        username = identifier.strip()
        if not username:
            return []
        out: list[BookRef] = []
        for shelf in ("to-read", "currently-reading"):
            out.extend(self._parse_shelf(self._fetch_shelf(username, shelf), username, shelf))
        return out
