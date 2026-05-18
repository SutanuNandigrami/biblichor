"""Tests for the audit-finding fixes:
1. SSRF guard (url_safety.assert_safe_url)
2. ISBN race in BookRepo.upsert (BEGIN IMMEDIATE + partial UNIQUE index)
3. VACUUM swap to PRAGMA wal_checkpoint
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from endless_library.db.books import BookRepo
from endless_library.db.events import EventRepo
from endless_library.db.schema import init_db
from endless_library.url_safety import UnsafeUrlError, assert_safe_url

# ============ SSRF guard ============


@pytest.mark.parametrize(
    "url",
    [
        "https://annas-archive.gl",
        "https://www.kindlebangla.com",
        "http://example.org/path?q=1",
        "https://example.com:8443/foo",
    ],
)
def test_assert_safe_url_allows_public(url):
    assert_safe_url(url)  # should not raise


@pytest.mark.parametrize(
    "url, reason_marker",
    [
        ("http://127.0.0.1:8090", "private"),
        ("http://localhost/api", "loopback"),
        ("http://localhost.localdomain", "loopback"),
        ("http://169.254.169.254/latest/meta-data", "private"),
        ("http://10.0.0.5/", "private"),
        ("http://192.168.1.1/", "private"),
        ("http://172.16.5.5/", "private"),
        ("http://0.0.0.0/", "private"),
        ("http://[::1]/", "private"),
        ("http://[fe80::1]/", "private"),
        ("file:///etc/passwd", "scheme"),
        ("javascript:alert(1)", "scheme"),
        ("gopher://example.com/_GET%20/", "scheme"),
        ("https://printer.local/", "internal"),
        ("https://server.internal/", "internal"),
        ("https://", "host"),
    ],
)
def test_assert_safe_url_blocks_unsafe(url, reason_marker):
    with pytest.raises(UnsafeUrlError, match=reason_marker):
        assert_safe_url(url)


def test_assert_safe_url_blocks_dns_to_private(monkeypatch):
    """A public-looking hostname that resolves to 127.0.0.1 must be blocked."""
    import socket

    from endless_library import url_safety

    def fake_getaddrinfo(host, port):
        # Pretend evil.example.com points at loopback
        if host == "evil.example.com":
            return [(socket.AF_INET, 0, 0, "", ("127.0.0.1", 0))]
        raise socket.gaierror

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError, match="resolves to"):
        assert_safe_url("https://evil.example.com")


# ============ ISBN race in upsert ============


def test_upsert_is_idempotent_for_same_isbn(tmp_path: Path):
    """Sanity: two sequential upserts with the same ISBN return the same id."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    bid1 = repo.upsert(
        title="X", author="A", isbn13="9780000000001", source="goodreads", source_id="gr1"
    )
    bid2 = repo.upsert(
        title="X different title",
        author="B",
        isbn13="9780000000001",
        source="hardcover",
        source_id="hc1",
    )
    assert bid1 == bid2


def test_upsert_partial_unique_index_blocks_dup_isbn(tmp_path: Path):
    """The partial UNIQUE index on isbn13 catches anything that slips past
    the SELECT-then-INSERT race. We assert the index exists and rejects
    a duplicate raw INSERT."""
    init_db(tmp_path / "library.db")
    with sqlite3.connect(tmp_path / "library.db") as conn:
        # First INSERT succeeds
        conn.execute(
            "INSERT INTO books (title, author, isbn13, source) VALUES (?, ?, ?, ?)",
            ("A", "x", "9780000000002", "manual"),
        )
        # Second INSERT with same isbn13 trips UNIQUE
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO books (title, author, isbn13, source) VALUES (?, ?, ?, ?)",
                ("B", "y", "9780000000002", "manual"),
            )


def test_upsert_allows_multiple_null_isbn(tmp_path: Path):
    """The partial index is `WHERE isbn13 IS NOT NULL` so books without
    ISBN can still co-exist."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    a = repo.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m1")
    b = repo.upsert(title="B", author=None, isbn13=None, source="manual", source_id="m2")
    assert a != b


def test_upsert_concurrent_dedup(tmp_path: Path):
    """The race the audit flagged: two threads upserting the same ISBN
    must both return the SAME id (one wins the BEGIN IMMEDIATE lock,
    the other re-reads the row that won)."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    isbn = "9780000000003"
    results: list[int] = []
    barrier = threading.Barrier(2)

    def insert():
        barrier.wait()
        bid = repo.upsert(
            title="The Race",
            author=None,
            isbn13=isbn,
            source="goodreads",
            source_id="gr-race",
        )
        results.append(bid)

    threads = [threading.Thread(target=insert) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2
    assert results[0] == results[1], f"race produced two ids: {results}"
    # And only one row exists for that ISBN
    with sqlite3.connect(tmp_path / "library.db") as conn:
        n = conn.execute("SELECT COUNT(*) FROM books WHERE isbn13 = ?", (isbn,)).fetchone()[0]
    assert n == 1


# ============ VACUUM swap ============


def test_prune_uses_wal_checkpoint_not_vacuum(tmp_path: Path):
    """events.prune used to call VACUUM (acquires EXCLUSIVE lock for many
    seconds). It now calls PRAGMA wal_checkpoint(TRUNCATE) which is
    non-blocking. Assert the source no longer mentions VACUUM in the
    prune function body."""
    import inspect

    src = inspect.getsource(EventRepo.prune)
    assert (
        "PRAGMA wal_checkpoint" in src and src.count("VACUUM") <= 2
    )  # mentions only in the comment explaining the swap
    assert "wal_checkpoint" in src


def test_prune_runs_cleanly_on_empty_db(tmp_path: Path):
    """And the swap should also not crash on an empty events table."""
    init_db(tmp_path / "library.db")
    repo = EventRepo(tmp_path / "library.db")
    n = repo.prune(keep_rows=100, keep_days=30)
    assert n == 0
