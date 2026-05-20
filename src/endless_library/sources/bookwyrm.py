"""BookWyrm (Fediverse / ActivityPub) reading-list source (Phase 6s.3).

Identifier format: '<instance-host>:<username>' (e.g.
'bookwyrm.social:alice'). Token: none (uses public ActivityPub
endpoints).

Fetches /user/<user>/books/to-read.json (the ActivityPub outbox).
Each entry has a stable Open Library work ID we use as the
source_id; ISBN resolution happens at intake via
metadata.openlibrary (Phase 6s.4).

Cleaner than StoryGraph because the ActivityPub schema is well-
documented and the OL ID is stable across renames.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import httpx

from endless_library.domain.models import BookRef

log = logging.getLogger(__name__)


class BookWyrm:
    name = "bookwyrm"

    def __init__(self, *, http_timeout: float = 15.0) -> None:
        self._timeout = http_timeout

    def _split_identifier(self, identifier: str) -> tuple[str, str] | None:
        if ":" not in identifier:
            return None
        host, user = identifier.split(":", 1)
        host = host.strip().rstrip("/")
        user = user.strip().lstrip("@")
        if not host or not user:
            return None
        return host, user

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        parts = self._split_identifier(identifier)
        if parts is None:
            log.warning("bookwyrm: identifier must be host:username, got %r", identifier)
            return []
        host, user = parts
        url = f"https://{host}/user/{user}/books/to-read.json"
        try:
            r = httpx.get(
                url,
                timeout=self._timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "endless-library/0.1",
                    "Accept": "application/activity+json",
                },
            )
        except httpx.HTTPError as e:
            log.warning("bookwyrm: %s", e)
            return []
        if r.status_code != 200:
            log.warning("bookwyrm: HTTP %s for %s", r.status_code, url)
            return []
        try:
            payload = r.json()
        except ValueError:
            return []
        items = payload.get("orderedItems") or payload.get("items") or []
        out: list[BookRef] = []
        for item in items:
            book = item.get("book") or item
            if not isinstance(book, dict):
                continue
            title = book.get("title") or book.get("name") or ""
            author = None
            if isinstance(book.get("authors"), list) and book["authors"]:
                a0 = book["authors"][0]
                if isinstance(a0, dict):
                    author = a0.get("name")
                elif isinstance(a0, str):
                    author = a0
            isbn13 = book.get("isbn13") or book.get("isbn_13") or None
            ol_id = book.get("openlibraryKey") or book.get("openlibrary_key") or book.get("id")
            if not title:
                continue
            out.append(
                BookRef(
                    title=title,
                    author=author,
                    isbn13=isbn13,
                    source="bookwyrm",
                    source_id=f"bookwyrm:{host}:{user}:{ol_id or title}",
                )
            )
        return out
