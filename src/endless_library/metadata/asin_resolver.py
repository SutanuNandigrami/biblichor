"""ASIN -> ISBN-13 helper (Phase 6s.4).

Thin facade that calls metadata.openlibrary first, with
extension points for additional resolvers (Hardcover search by
title) when OpenLibrary misses.
"""

from __future__ import annotations

import logging
from pathlib import Path

from endless_library.metadata import openlibrary

log = logging.getLogger(__name__)


def asin_to_isbn(asin: str, db_path: Path | None = None) -> str | None:
    """Try OpenLibrary first; return None if no ISBN known."""
    if not asin:
        return None
    isbn = openlibrary.resolve_by_asin(asin, db_path=db_path)
    if isbn:
        return isbn
    log.info("asin_resolver: no ISBN for ASIN %s in any source", asin)
    return None
