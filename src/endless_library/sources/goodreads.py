from __future__ import annotations

from collections.abc import Iterable

import feedparser
import httpx

from endless_library.domain.models import BookRef
from endless_library.sources.base import normalize_isbn

GOODREADS_RSS = "https://www.goodreads.com/review/list_rss/{user_id}?shelf={shelf}"


class GoodreadsRSS:
    name = "goodreads"

    def __init__(self, *, http_timeout: float = 30.0, fetch: callable | None = None) -> None:
        self._timeout = http_timeout
        self._fetch = fetch  # for tests: a callable(url) -> str

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        # identifier format: "<user_id>:<shelf>"
        if ":" in identifier:
            user_id, shelf = identifier.split(":", 1)
        else:
            user_id, shelf = identifier, "to-read"
        url = GOODREADS_RSS.format(user_id=user_id, shelf=shelf)
        xml = self._get(url)
        return list(self._parse(xml))

    def _get(self, url: str) -> str:
        if self._fetch is not None:
            return self._fetch(url)
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "endless-library/0.1"})
            r.raise_for_status()
            return r.text

    @staticmethod
    def _parse(xml: str) -> Iterable[BookRef]:
        feed = feedparser.parse(xml)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            author = (entry.get("author_name") or entry.get("author") or "").strip() or None
            isbn = normalize_isbn(entry.get("isbn") or entry.get("isbn13"))
            # Goodreads review id is in <guid> or <book_id>
            sid = entry.get("book_id") or entry.get("guid") or entry.get("id") or title
            added = entry.get("user_date_added") or entry.get("published")
            if not title or not sid:
                continue
            yield BookRef(
                title=title,
                author=author,
                isbn13=isbn,
                source="goodreads",
                source_id=str(sid),
                source_added_at=str(added) if added else None,
            )
