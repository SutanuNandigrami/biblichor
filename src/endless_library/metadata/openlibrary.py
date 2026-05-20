"""OpenLibrary primary metadata resolver (Phase 6s.4).

Canonical ISBN-to-everything lookup with 30-day cache in
sqlite (metadata_cache table). All callers go through here
rather than reimplementing per-source.

Functions:
  resolve_by_isbn(isbn, db_path=None) -> dict | None
  resolve_by_title_author(title, author=None, db_path=None) -> dict | None
  resolve_by_asin(asin, db_path=None) -> str | None  # returns ISBN
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from endless_library.db.schema import connect

log = logging.getLogger(__name__)

OL_BASE = "https://openlibrary.org"
CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def _cache_get(db_path: Path | None, key: str) -> dict | None:
    if db_path is None:
        return None
    try:
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM metadata_cache WHERE key=?",
                (key,),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    payload, fetched_at = row[0], row[1]
    if int(time.time()) - int(fetched_at) > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(payload)
    except (TypeError, ValueError):
        return None


def _cache_put(db_path: Path | None, key: str, payload: dict) -> None:
    if db_path is None:
        return
    try:
        with connect(db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metadata_cache (key, payload, fetched_at) VALUES (?, ?, ?)",
                (key, json.dumps(payload), int(time.time())),
            )
            conn.commit()
    except Exception as e:
        log.info("metadata_cache write %s: %s", key, e)


def _ol_get(url: str, params: dict | None = None) -> dict | None:
    try:
        r = httpx.get(
            url,
            params=params or {},
            timeout=15.0,
            headers={
                "User-Agent": "endless-library/0.1",
                "Accept": "application/json",
            },
        )
    except httpx.HTTPError as e:
        log.info("openlibrary %s: %s", url, e)
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def resolve_by_isbn(isbn: str, db_path: Path | None = None) -> dict | None:
    """ISBN -> {title, authors, publish_date, first_publish_year,
    subjects, identifiers, cover_url}."""
    if not isbn:
        return None
    isbn = isbn.replace("-", "").strip()
    if not isbn:
        return None
    cache_key = f"ol:isbn:{isbn}"
    cached = _cache_get(db_path, cache_key)
    if cached is not None:
        return cached
    body = _ol_get(
        f"{OL_BASE}/api/books",
        {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
    )
    if not body:
        return None
    entry = body.get(f"ISBN:{isbn}")
    if not entry:
        return None
    out = {
        "title": entry.get("title", ""),
        "authors": [a.get("name", "") for a in entry.get("authors", [])],
        "publish_date": entry.get("publish_date"),
        "subjects": [s.get("name") for s in entry.get("subjects", []) if isinstance(s, dict)],
        "cover_url": (entry.get("cover") or {}).get("large"),
        "identifiers": entry.get("identifiers", {}),
    }
    _cache_put(db_path, cache_key, out)
    return out


def resolve_by_title_author(
    title: str, author: str | None = None, db_path: Path | None = None
) -> dict | None:
    """Fuzzy title+author search against /search.json. Returns top
    match's basic fields including first_publish_year and isbn (if
    OL has one)."""
    if not title:
        return None
    key_author = (author or "").strip().lower()
    cache_key = f"ol:search:{title.strip().lower()}|{key_author}"
    cached = _cache_get(db_path, cache_key)
    if cached is not None:
        return cached
    params: dict[str, str] = {"title": title, "limit": "1"}
    if author:
        params["author"] = author
    body = _ol_get(f"{OL_BASE}/search.json", params)
    if not body:
        return None
    docs = body.get("docs", [])
    if not docs:
        return None
    doc = docs[0]
    out = {
        "title": doc.get("title", ""),
        "authors": doc.get("author_name", []),
        "first_publish_year": doc.get("first_publish_year"),
        "isbn": (doc.get("isbn") or [None])[0],
        "work_key": doc.get("key"),
    }
    _cache_put(db_path, cache_key, out)
    return out


def resolve_by_asin(asin: str, db_path: Path | None = None) -> str | None:
    """ASIN -> ISBN-13 via Open Library /api/books?bibkeys=ASIN:<asin>."""
    if not asin:
        return None
    asin = asin.strip().upper()
    cache_key = f"ol:asin:{asin}"
    cached = _cache_get(db_path, cache_key)
    if cached is not None:
        return (cached or {}).get("isbn") or None
    body = _ol_get(
        f"{OL_BASE}/api/books",
        {"bibkeys": f"ASIN:{asin}", "format": "json", "jscmd": "data"},
    )
    if not body:
        return None
    entry = body.get(f"ASIN:{asin}")
    if not entry:
        return None
    isbns = (entry.get("identifiers") or {}).get("isbn_13") or []
    isbn = isbns[0] if isbns else None
    if not isbn:
        isbns_10 = (entry.get("identifiers") or {}).get("isbn_10") or []
        isbn = isbns_10[0] if isbns_10 else None
    _cache_put(db_path, cache_key, {"isbn": isbn})
    return isbn
