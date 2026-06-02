from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import feedparser
import httpx

from endless_library.domain.models import BookRef
from endless_library.sources.base import normalize_isbn

log = logging.getLogger(__name__)

GOODREADS_RSS = "https://www.goodreads.com/review/list_rss/{user_id}?shelf={shelf}"
GOODREADS_BOOK_URL = "https://www.goodreads.com/book/show/{book_id}"

# user_id is always a digit sequence in Goodreads URLs.
_USER_ID_IN_PATH = re.compile(r"/(?:review/list|user/show)/(\d+)")
# Goodreads embeds book metadata as JSON-LD: `"isbn":"9780123456789"`.
# We grab the digits straight out — robust against whitespace/key-ordering
# differences across editions of the book detail page.
_ISBN_LD_RE = re.compile(r'"isbn"\s*:\s*"(\d{10,13})"')


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

    def __init__(
        self,
        *,
        http_timeout: float = 30.0,
        fetch: callable | None = None,
        fetch_isbn: bool = True,
    ) -> None:
        self._timeout = http_timeout
        self._fetch = fetch  # for tests: a callable(url) -> str
        self._fetch_isbn_enabled = fetch_isbn

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        user_id, shelf = _parse_goodreads_identifier(identifier)
        url = GOODREADS_RSS.format(user_id=user_id, shelf=shelf)
        xml = self._get(url)
        entries = list(self._parse(xml))
        if not self._fetch_isbn_enabled:
            return entries
        # Backfill ISBN from each book's detail page when the RSS feed
        # didn't include one. The Goodreads RSS only carries `isbn` for
        # some shelf/edition combos; most user shelves come back empty,
        # which leaves the scorer 35 points short of its auto-pick floor.
        return [self._with_fetched_isbn(e) for e in entries]

    def _with_fetched_isbn(self, ref: BookRef) -> BookRef:
        if ref.isbn13 or not ref.source_id or not ref.source_id.isdigit():
            return ref
        isbn = self.fetch_isbn(ref.source_id)
        if isbn:
            return replace(ref, isbn13=isbn)
        return ref

    def fetch_isbn(self, goodreads_book_id: str) -> str | None:
        """Best-effort scrape of ISBN-13 from the book's Goodreads detail
        page (JSON-LD `isbn` field). Returns None on any failure — the
        caller treats this as 'no ISBN available' and the scoring
        pipeline degrades to title+author matching."""
        url = GOODREADS_BOOK_URL.format(book_id=goodreads_book_id)
        try:
            html = self._get(url)
        except Exception as e:
            log.debug("goodreads ISBN fetch failed for %s: %s", goodreads_book_id, e)
            return None
        m = _ISBN_LD_RE.search(html or "")
        if not m:
            return None
        return normalize_isbn(m.group(1))

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


def backfill_isbns(db_path, *, dry_run: bool = False, throttle_sec: float = 0.4) -> dict:
    """Scan books where isbn13 is NULL but goodreads_id is set, fetch
    each from Goodreads, and patch the row. Returns counts.

    throttle_sec sleeps between fetches so we don't hammer Goodreads."""
    import time

    from endless_library.db.schema import connect

    src = GoodreadsRSS(fetch_isbn=False)  # disable per-entry; we drive directly
    updated, no_isbn, errored = 0, 0, 0
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, goodreads_id FROM books
               WHERE isbn13 IS NULL AND goodreads_id IS NOT NULL"""
        ).fetchall()
        total = len(rows)
        for r in rows:
            try:
                isbn = src.fetch_isbn(str(r["goodreads_id"]))
            except Exception as e:
                log.warning("backfill: fetch failed for book #%s: %s", r["id"], e)
                errored += 1
                continue
            if isbn:
                if not dry_run:
                    conn.execute(
                        "UPDATE books SET isbn13 = ?, updated_at = datetime('now') WHERE id = ?",
                        (isbn, r["id"]),
                    )
                updated += 1
            else:
                no_isbn += 1
            time.sleep(throttle_sec)
    return {
        "total": total,
        "updated": updated,
        "no_isbn": no_isbn,
        "errored": errored,
        "dry_run": dry_run,
    }
