"""Smoke test for the dedups_24h KPI added in PR #49.

The md5 dedup machinery in pipeline._resolve_and_download parks a
duplicate row as status='skipped' + last_error like
"duplicate of book #N". The dashboard snapshot exposes the count
within the rolling 24h window via kpis.dedups_24h. Confirm the
SELECT matches the right rows + ignores unrelated 'skipped' rows.
"""

from __future__ import annotations

from pathlib import Path

from endless_library.db.schema import connect, init_db
from endless_library.web.api import compute_dashboard_snapshot


def _seed_book(conn, *, status, last_error=None, ts_offset_hours=0):
    conn.execute(
        """
        INSERT INTO books (
            title, author, source, goodreads_id, status, last_error,
            updated_at
        )
        VALUES (?, 'a', 'goodreads', ?, ?, ?, datetime('now', ?))
        """,
        (
            "t",
            f"g-{ts_offset_hours}-{status}-{last_error or ''}",
            status,
            last_error,
            f"-{ts_offset_hours} hours",
        ),
    )
    conn.commit()


def test_dedups_24h_counts_only_duplicate_skipped_rows(tmp_path: Path):
    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        # Two genuine dedups within the window.
        _seed_book(
            conn,
            status="skipped",
            last_error="duplicate of book #42",
            ts_offset_hours=1,
        )
        _seed_book(
            conn,
            status="skipped",
            last_error="duplicate of book #43",
            ts_offset_hours=6,
        )
        # A skipped row that is NOT a dedup -- must not count.
        _seed_book(
            conn,
            status="skipped",
            last_error="max attempts exceeded",
            ts_offset_hours=2,
        )
        # A dedup-shaped row that is older than 24h -- must not count.
        _seed_book(
            conn,
            status="skipped",
            last_error="duplicate of book #99",
            ts_offset_hours=30,
        )
        # A duplicate-like message but on a non-skipped row -- ignored.
        _seed_book(
            conn,
            status="failed",
            last_error="duplicate of book #7",
            ts_offset_hours=1,
        )

    snap = compute_dashboard_snapshot(db)
    assert snap["kpis"]["dedups_24h"] == 2


def test_dedups_24h_zero_when_no_dedups(tmp_path: Path):
    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        _seed_book(conn, status="sent", ts_offset_hours=1)
        _seed_book(conn, status="queued", ts_offset_hours=2)
    snap = compute_dashboard_snapshot(db)
    assert snap["kpis"]["dedups_24h"] == 0
