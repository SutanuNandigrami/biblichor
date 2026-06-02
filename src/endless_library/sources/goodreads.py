from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, urlparse

import feedparser
import httpx

from endless_library.domain.models import BookRef
from endless_library.sources.base import normalize_isbn

GOODREADS_RSS = "https://www.goodreads.com/review/list_rss/{user_id}?shelf={shelf}"

# user_id is always a digit sequence in Goodreads URLs.
_USER_ID_IN_PATH = re.compile(r"/(?:review/list|user/show)/(\d+)")


def _parse_goodreads_identifier(identifier: str) -> tuple[str, str]:
    """Accept any of:
      - Plain user id:      "69278726"
      - user_id:shelf form: "69278726:books-movie-english"
      - Full URL:           "https://www.goodreads.com/review/list/69278726?shelf=books-movie-english"
      - URL without scheme: "goodreads.com/review/list/69278726?shelf=books-movie-english"

    Returns (user_id, shelf). Shelf defaults to "to-read" if not present.
    """
    if not identifier or not identifier.strip():
        raise ValueError("Goodreads identifier is empty")
    s = identifier.strip()

    # URL-ish? Parse it.
    if "/" in s or "?" in s:
        # urlparse needs a scheme to give sensible netloc/path; bolt one on.
        parsed = urlparse(s if "://" in s else "https://" + s)
        m = _USER_ID_IN_PATH.search(parsed.path)
        if not m:
            # Fall back: maybe the path itself is just the user id.
            tail = (parsed.path or "").strip("/").split("/")[-1]
            if tail.isdigit():
                user_id = tail
            else:
                raise ValueError(
                    f"Could not extract Goodreads user id from {identifier!r}. "
                    "Expected a URL like https://www.goodreads.com/review/list/12345"
                )
        else:
            user_id = m.group(1)
        shelf_vals = parse_qs(parsed.query).get("shelf") or ["to-read"]
        return user_id, shelf_vals[0]

    # Colon form.
    if ":" in s:
        user_id, shelf = s.split(":", 1)
        return user_id.strip(), (shelf.strip() or "to-read")

    # Plain id.
    return s, "to-read"


class GoodreadsRSS:
    name = "goodreads"

    def __init__(self, *, http_timeout: float = 30.0, fetch: callable | None = None) -> None:
        self._timeout = http_timeout
        self._fetch = fetch  # for tests: a callable(url) -> str

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        user_id, shelf = _parse_goodreads_identifier(identifier)
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
