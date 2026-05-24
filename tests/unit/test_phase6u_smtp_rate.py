"""Phase 6u — SMTP rate-limit gate tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from endless_library.kindle import KindleRateLimited, KindleSendError, _looks_like_rate_limit
from endless_library.smtp_rate import quota_status


@pytest.fixture
def db_with_events(tmp_path) -> Path:
    db = tmp_path / "rate.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER,
            ts TEXT DEFAULT CURRENT_TIMESTAMP,
            kind TEXT,
            scraper TEXT,
            message TEXT,
            meta_json TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return db


def _insert_send(db: Path, n: int, *, age_offset: str = "0 hours") -> None:
    conn = sqlite3.connect(db)
    for _ in range(n):
        conn.execute(
            "INSERT INTO events (book_id, kind, message, ts) VALUES (?,?,?, datetime('now', ?))",
            (1, "send-smtp", "sent to kindle", f"-{age_offset}"),
        )
    conn.commit()
    conn.close()


def test_empty_db_under_cap(db_with_events: Path) -> None:
    q = quota_status(db_with_events, daily_cap=80)
    assert q.sent_24h == 0
    assert q.cap == 80
    assert q.remaining == 80
    assert not q.exhausted


def test_counts_only_send_kind(db_with_events: Path) -> None:
    _insert_send(db_with_events, 3)
    # Add some non-send events that must not count
    conn = sqlite3.connect(db_with_events)
    for kind in ("scrape", "error", "auto_pick", "send_deferred"):
        conn.execute("INSERT INTO events (book_id, kind, message) VALUES (?,?,?)", (1, kind, "x"))
    conn.commit()
    conn.close()

    q = quota_status(db_with_events, daily_cap=80)
    assert q.sent_24h == 3
    assert q.remaining == 77


def test_old_sends_outside_24h_are_ignored(db_with_events: Path) -> None:
    _insert_send(db_with_events, 5, age_offset="2 days")
    _insert_send(db_with_events, 7, age_offset="0 hours")
    q = quota_status(db_with_events, daily_cap=80)
    assert q.sent_24h == 7


def test_exhausted_when_at_or_above_cap(db_with_events: Path) -> None:
    _insert_send(db_with_events, 80)
    q = quota_status(db_with_events, daily_cap=80)
    assert q.exhausted
    assert q.remaining == 0


def test_zero_cap_disables_the_gate(db_with_events: Path) -> None:
    _insert_send(db_with_events, 500)
    q = quota_status(db_with_events, daily_cap=0)
    assert q.cap == 0
    assert q.sent_24h == 0  # short-circuit avoids the DB hit
    assert not q.exhausted


@pytest.mark.parametrize(
    "msg",
    [
        "421 4.7.0 Try again later, closing connection",
        "452 4.5.3 Domain message limit exceeded",
        "550 5.4.5 Daily user sending limit exceeded",
        "4.7.28 gmail per-IP throttle observed",
        "Rate limit hit on outbound",
    ],
)
def test_looks_like_rate_limit_recognises_gmail_codes(msg: str) -> None:
    assert _looks_like_rate_limit(msg) is True


def test_looks_like_rate_limit_rejects_unrelated_errors() -> None:
    assert _looks_like_rate_limit("552 message too large") is False
    assert _looks_like_rate_limit("authentication failed") is False
    assert _looks_like_rate_limit("connection refused") is False


def test_rate_limited_is_a_send_error_subclass() -> None:
    # Pipeline catches KindleSendError as the broad fallback; the more
    # specific KindleRateLimited must remain a subclass so that ordering
    # of `except` blocks (rate-limited first, then generic) works.
    assert issubclass(KindleRateLimited, KindleSendError)
