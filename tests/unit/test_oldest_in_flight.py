"""Smoke test for the oldest_in_flight_minutes KPI added in PR #52.

The KPI surfaces how stuck the most-stale in-flight book is. Would have
made the 2026-06-17 fan-out hang visible immediately instead of needing
manual SQL. Returns 0 when nothing is in-flight.
"""

from __future__ import annotations

from pathlib import Path

from endless_library.db.schema import connect, init_db
from endless_library.web.api import compute_dashboard_snapshot


def _seed(conn, *, status, age_minutes=0):
    conn.execute(
        """
        INSERT INTO books (title, author, source, goodreads_id, status, updated_at)
        VALUES ('t', 'a', 'goodreads', ?, ?, datetime('now', ?))
        """,
        (
            f"g-{status}-{age_minutes}",
            status,
            f"-{age_minutes} minutes",
        ),
    )
    conn.commit()


def test_oldest_in_flight_minutes_picks_max_age(tmp_path: Path):
    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        _seed(conn, status="searching", age_minutes=5)
        _seed(conn, status="downloading", age_minutes=12)
        _seed(conn, status="sending", age_minutes=2)
        # Completed books with old updated_at must NOT count.
        _seed(conn, status="sent", age_minutes=120)
        _seed(conn, status="failed", age_minutes=60)
    snap = compute_dashboard_snapshot(db)
    # Tolerate -1/+1 minute slack from julianday float math
    assert 11 <= snap["kpis"]["oldest_in_flight_minutes"] <= 13


def test_oldest_in_flight_minutes_zero_when_nothing_in_flight(tmp_path: Path):
    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        _seed(conn, status="queued", age_minutes=5)
        _seed(conn, status="sent", age_minutes=10)
    snap = compute_dashboard_snapshot(db)
    assert snap["kpis"]["oldest_in_flight_minutes"] == 0


def test_oldest_in_flight_minutes_handles_all_four_inflight_states(tmp_path: Path):
    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        # Each of the 4 states counts; converting is the oldest here.
        _seed(conn, status="searching", age_minutes=1)
        _seed(conn, status="downloading", age_minutes=2)
        _seed(conn, status="converting", age_minutes=45)
        _seed(conn, status="sending", age_minutes=3)
    snap = compute_dashboard_snapshot(db)
    assert 44 <= snap["kpis"]["oldest_in_flight_minutes"] <= 46
