"""Phase STK 5: stk_rate.quota_status — STK delivery rate-limit gate."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from endless_library.db.schema import init_db


@pytest.fixture
def db(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p)
    return p


def _record_event(db: Path, kind: str, ts_offset_sec: int = 0) -> None:
    """Insert an events row at NOW + ts_offset_sec."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO events (kind, book_id, meta_json, message, ts) "
            "VALUES (?, NULL, '{}', '', datetime('now', ?))",
            (kind, f"{ts_offset_sec:+d} seconds"),
        )


def test_quota_status_zero_when_no_events(db):
    from endless_library.stk_rate import quota_status
    s = quota_status(db, daily_cap=500)
    assert s.sent_24h == 0
    assert s.cap == 500
    assert s.remaining == 500
    assert s.exhausted is False


def test_quota_status_counts_only_send_stk_kind(db):
    _record_event(db, "send-stk")
    _record_event(db, "send-stk")
    _record_event(db, "send")        # SMTP — must not count
    _record_event(db, "search")      # noise — must not count
    from endless_library.stk_rate import quota_status
    s = quota_status(db, daily_cap=500)
    assert s.sent_24h == 2
    assert s.remaining == 498


def test_quota_status_respects_24h_window(db):
    _record_event(db, "send-stk", ts_offset_sec=-90_000)  # 25 hours ago
    _record_event(db, "send-stk", ts_offset_sec=-3600)    # 1 hour ago
    from endless_library.stk_rate import quota_status
    s = quota_status(db, daily_cap=500)
    assert s.sent_24h == 1


def test_quota_status_exhausted_when_at_cap(db):
    for _ in range(5):
        _record_event(db, "send-stk")
    from endless_library.stk_rate import quota_status
    s = quota_status(db, daily_cap=5)
    assert s.exhausted is True
    assert s.remaining == 0
