"""NYT Best Sellers via the official Books API (Phase 6s.3).

Identifier format: list slug (e.g. 'hardcover-fiction').
Token: NYT API key (free, developer.nytimes.com).
Free tier: 1000 req/day, 5 req/min. We poll weekly per list so
usage is well under cap.

Returns ISBN-13 + title + author per entry — cleanest source in
the bunch (no scraping; official API).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from endless_library.domain.models import BookRef

log = logging.getLogger(__name__)


class NYTBestSellers:
    name = "nyt"

    def __init__(self, *, http_timeout: float = 15.0) -> None:
        self._timeout = http_timeout

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        if not token:
            log.warning("nyt: API key (token) required")
            return []
        url = f"https://api.nytimes.com/svc/books/v3/lists/current/{identifier}.json"
        try:
            r = httpx.get(
                url,
                params={"api-key": token},
                timeout=self._timeout,
                headers={"User-Agent": "endless-library/0.1"},
            )
        except httpx.HTTPError as e:
            log.warning("nyt: %s", e)
            return []
        if r.status_code != 200:
            log.warning("nyt: HTTP %s for list=%s", r.status_code, identifier)
            return []
        out: list[BookRef] = []
        for b in r.json().get("results", {}).get("books", []):
            isbn13 = b.get("primary_isbn13") or None
            title = b.get("title", "")
            if not (title or isbn13):
                continue
            out.append(
                BookRef(
                    title=title,
                    author=b.get("author") or None,
                    isbn13=isbn13,
                    source="nyt",
                    source_id=f"nyt:{identifier}:{isbn13 or title}",
                )
            )
        return out
