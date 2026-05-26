"""Phase STK 7: books.sent_method TEXT NULL column."""

from __future__ import annotations

from pathlib import Path

from endless_library.db.schema import connect, init_db


def test_books_has_sent_method_column_after_init_db(tmp_path: Path):
    db = tmp_path / "test.db"
    init_db(db)
    with connect(db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)")}
    assert "sent_method" in cols


def test_books_sent_method_default_is_null(tmp_path: Path):
    db = tmp_path / "test.db"
    init_db(db)
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, source, status) VALUES ('Test', 'test', 'queued')"
        )
        book_id = cur.lastrowid
        row = conn.execute("SELECT sent_method FROM books WHERE id = ?", (book_id,)).fetchone()
    assert row["sent_method"] is None


def test_mark_kindled_records_method(tmp_path: Path):
    """books_repo.mark_kindled(book_id, method='stk') stores 'stk'."""
    from endless_library.db.books import BookRepo

    db = tmp_path / "test.db"
    init_db(db)
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, source, status) VALUES ('Test', 'test', 'queued')"
        )
        book_id = cur.lastrowid
    repo = BookRepo(db)
    repo.mark_kindled(book_id, method="stk")
    with connect(db) as conn:
        row = conn.execute(
            "SELECT status, sent_method FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    assert row["status"] == "kindled"
    assert row["sent_method"] == "stk"


def test_mark_kindled_without_method_leaves_null(tmp_path: Path):
    """Backwards compat — old call sites without `method=` still work."""
    from endless_library.db.books import BookRepo

    db = tmp_path / "test.db"
    init_db(db)
    with connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO books (title, source, status) VALUES ('Test', 'test', 'queued')"
        )
        book_id = cur.lastrowid
    repo = BookRepo(db)
    repo.mark_kindled(book_id)  # no method
    with connect(db) as conn:
        row = conn.execute(
            "SELECT status, sent_method FROM books WHERE id = ?", (book_id,)
        ).fetchone()
    assert row["status"] == "kindled"
    assert row["sent_method"] is None
