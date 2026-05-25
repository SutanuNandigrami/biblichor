"""Regression test for the sent_method backfill in schema._migrate.

The sent_method column was added mid-flight (2026-05-24). Old rows
were left NULL, which silently undercounted historical SMTP volume
in any analytics that group by method. The backfill in _migrate
sets NULL -> 'smtp' for rows already in sent/kindled status.

Audit: 316 rows on prod fit this profile. All have sent_at strictly
before the first send-stk audit event (verified 2026-05-25). Safe to
attribute to SMTP.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from endless_library.db.schema import init_db


def _seed_pre_migration_row(db_path: Path, *, status: str, sent_method=None) -> int:
    """Insert a row as if the DB pre-dates the migration: schema is already
    current (init_db ran), but sent_method is NULL on this row."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO books (title, author, status, sent_method, source, "
        "created_at, updated_at) "
        "VALUES (?, '', ?, ?, 'manual', datetime('now'), datetime('now'))",
        (f"book-{status}-{sent_method or 'NULL'}", status, sent_method),
    )
    rid = cur.lastrowid
    conn.commit()
    conn.close()
    return rid


def test_backfill_sets_smtp_on_null_sent_rows(tmp_path: Path) -> None:
    """init_db runs migrations idempotently. Seeding NULL rows after init
    and re-running init_db must coerce them to 'smtp'."""
    db = tmp_path / "test.db"
    init_db(db)

    sent_row = _seed_pre_migration_row(db, status="sent")
    kindled_row = _seed_pre_migration_row(db, status="kindled")

    # Re-run migrations (this is the path that runs at every container boot).
    init_db(db)

    conn = sqlite3.connect(db)
    for rid in (sent_row, kindled_row):
        method = conn.execute(
            "SELECT sent_method FROM books WHERE id=?", (rid,)
        ).fetchone()[0]
        assert method == "smtp", f"row {rid} not backfilled: got {method!r}"
    conn.close()


def test_backfill_leaves_non_sent_rows_alone(tmp_path: Path) -> None:
    """needs_review / queued / failed rows with NULL sent_method are NOT
    sent and must stay NULL. The backfill should target only rows in
    sent/kindled."""
    db = tmp_path / "test.db"
    init_db(db)

    queued = _seed_pre_migration_row(db, status="queued")
    needs_review = _seed_pre_migration_row(db, status="needs_review")
    failed = _seed_pre_migration_row(db, status="failed")

    init_db(db)

    conn = sqlite3.connect(db)
    for rid in (queued, needs_review, failed):
        method = conn.execute(
            "SELECT sent_method FROM books WHERE id=?", (rid,)
        ).fetchone()[0]
        assert method is None, (
            f"non-sent row {rid} was incorrectly backfilled to {method!r}"
        )
    conn.close()


def test_backfill_does_not_overwrite_existing_method(tmp_path: Path) -> None:
    """Idempotency: if a row already has sent_method='stk' (a newer row
    correctly recorded), the backfill must NOT change it to 'smtp'."""
    db = tmp_path / "test.db"
    init_db(db)

    stk_row = _seed_pre_migration_row(db, status="kindled", sent_method="stk")

    init_db(db)

    conn = sqlite3.connect(db)
    method = conn.execute(
        "SELECT sent_method FROM books WHERE id=?", (stk_row,)
    ).fetchone()[0]
    assert method == "stk", (
        f"backfill clobbered an existing sent_method='stk' -> {method!r}"
    )
    conn.close()


def test_backfill_idempotent_on_fresh_db(tmp_path: Path) -> None:
    """A fresh DB has no rows, so the backfill UPDATE matches zero rows.
    Two consecutive init_db calls must not raise or change row count."""
    db = tmp_path / "test.db"
    init_db(db)
    init_db(db)
    init_db(db)

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    assert count == 0
    conn.close()
