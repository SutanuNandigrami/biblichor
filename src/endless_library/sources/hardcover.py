from __future__ import annotations

from collections.abc import Iterable

import httpx

from endless_library.domain.models import BookRef
from endless_library.sources.base import normalize_isbn

HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"

# status_id=1 → "Want to Read"
QUERY = """
query MyWantToRead {
  me {
    user_books(where: { status_id: { _eq: 1 } }) {
      id
      updated_at
      book {
        id
        title
        contributions { author { name } }
        editions(where: { isbn_13: { _is_null: false } }, limit: 1) {
          isbn_13
        }
      }
    }
  }
}
"""


class HardcoverGQL:
    name = "hardcover"

    def __init__(self, *, http_timeout: float = 30.0, post: callable | None = None) -> None:
        self._timeout = http_timeout
        self._post = post  # for tests: callable(url, json, headers) -> dict

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]:
        if not token:
            raise ValueError("Hardcover requires an API token")
        data = self._gql(token)
        for ub in data.get("me", [{}])[0].get("user_books", []):
            book = ub.get("book") or {}
            title = (book.get("title") or "").strip()
            if not title:
                continue
            contribs = book.get("contributions") or []
            author = None
            if contribs:
                author = (contribs[0].get("author") or {}).get("name")
            editions = book.get("editions") or []
            isbn = normalize_isbn(editions[0]["isbn_13"]) if editions else None
            yield BookRef(
                title=title,
                author=author,
                isbn13=isbn,
                source="hardcover",
                source_id=str(ub.get("id") or book.get("id") or title),
                source_added_at=ub.get("updated_at"),
            )

    def _gql(self, token: str) -> dict:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = {"query": QUERY}
        if self._post is not None:
            body = self._post(HARDCOVER_URL, payload, headers)
        else:
            with httpx.Client(timeout=self._timeout) as c:
                r = c.post(HARDCOVER_URL, json=payload, headers=headers)
                r.raise_for_status()
                body = r.json()
        if "errors" in body:
            raise RuntimeError(f"Hardcover GraphQL errors: {body['errors']}")
        return body.get("data") or {}
