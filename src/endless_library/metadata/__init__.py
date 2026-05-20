"""Centralized metadata helpers (Phase 6s.4).

External catalog resolvers (Open Library, Hardcover, etc.) used
by the sources and scrapers to fill in missing ISBNs, cover
URLs, and series ordering. All lookups go through a single
metadata_cache table with a 30-day TTL.
"""

from endless_library.metadata.asin_resolver import asin_to_isbn
from endless_library.metadata.openlibrary import (
    resolve_by_asin,
    resolve_by_isbn,
    resolve_by_title_author,
)

__all__ = [
    "asin_to_isbn",
    "resolve_by_asin",
    "resolve_by_isbn",
    "resolve_by_title_author",
]
