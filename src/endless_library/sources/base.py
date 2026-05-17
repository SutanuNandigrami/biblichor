from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from endless_library.domain.models import BookRef


class Source(Protocol):
    """A reading-list adapter (Goodreads, Hardcover, Manual)."""

    name: str

    def list_to_read(self, *, identifier: str, token: str | None) -> Iterable[BookRef]: ...


def normalize_isbn(raw: str | None) -> str | None:
    """Strip hyphens/spaces; return only 13-digit ISBNs, else None."""
    if not raw:
        return None
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 13:
        return digits
    if len(digits) == 10:
        # ISBN-10 -> ISBN-13 (978 prefix + recompute checksum)
        body = "978" + digits[:9]
        s = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(body))
        check = (10 - s % 10) % 10
        return body + str(check)
    return None
