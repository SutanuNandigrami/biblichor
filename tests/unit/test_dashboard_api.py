"""Unit tests for compute_dashboard_snapshot().

Tests exercise the pure aggregation function directly — no FastAPI deps.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pytest

from endless_library.db.schema import connect, init_db
from datetime import UTC
from endless_library.web.api import compute_dashboard_snapshot

# Counter for unique goodreads_id values
_counter = 0


def _next_id() -> str:
    global _counter
    _counter += 1
    return str(_counter)


# ──────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────


def _seed_book(
    conn, *, source="goodreads", status="queued", file_path=None, sent_method=None, sent_at=None
):
    gid = _next_id()
    conn.execute(
        """
        INSERT INTO books
            (title, author, source, goodreads_id, status, file_path, sent_method, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"Book-{gid}",
            "Author",
            source,
            gid,
            status,
            file_path,
            sent_method,
            sent_at,
        ),
    )
    conn.commit()


def _seed_event(conn, *, kind, ts_offset_hours=0):
    """Insert an event ts_offset_hours before now (positive = in the past)."""
    conn.execute(
        """
        INSERT INTO events (ts, kind, message)
        VALUES (datetime('now', ?), ?, 'test event')
        """,
        (f"-{ts_offset_hours} hours", kind),
    )
    conn.commit()


# ──────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


# ──────────────────────────────────────────────────────────
# 1. Status counts
# ──────────────────────────────────────────────────────────


def test_snapshot_returns_status_counts(db: Path):
    with connect(db) as conn:
        _seed_book(conn, status="queued", source="goodreads")
        _seed_book(conn, status="queued", source="goodreads")
        _seed_book(conn, status="kindled", source="goodreads")
        _seed_book(conn, status="failed", source="goodreads")

    snap = compute_dashboard_snapshot(db)
    assert snap["status_counts"]["queued"] == 2
    assert snap["status_counts"]["kindled"] == 1
    assert snap["status_counts"]["failed"] == 1
    # Absent statuses should not appear
    assert "sent" not in snap["status_counts"]


def test_snapshot_has_required_top_level_keys(db: Path):
    snap = compute_dashboard_snapshot(db)
    for key in ("ts", "status_counts", "throughput_24h", "method_breakdown_24h", "source_funnel"):
        assert key in snap, f"missing key: {key}"


# ──────────────────────────────────────────────────────────
# 2. Throughput bucketing
# ──────────────────────────────────────────────────────────


def test_snapshot_throughput_buckets_5min(db: Path):
    with connect(db) as conn:
        # 1 stk event 1h ago (within 24h window)
        _seed_event(conn, kind="send-stk", ts_offset_hours=1)
        # 1 smtp event 2h ago
        _seed_event(conn, kind="send", ts_offset_hours=2)
        # 1 stk event 25h ago (outside window — should be excluded)
        _seed_event(conn, kind="send-stk", ts_offset_hours=25)

    snap = compute_dashboard_snapshot(db)
    tp = snap["throughput_24h"]
    assert tp["bucket_minutes"] == 5

    series = {s["name"]: s["points"] for s in tp["series"]}
    assert "stk" in series
    assert "smtp" in series

    # Total stk events within window should be 1
    stk_total = sum(p["v"] for p in series["stk"])
    assert stk_total == 1, f"expected 1 stk event, got {stk_total}"

    smtp_total = sum(p["v"] for p in series["smtp"])
    assert smtp_total == 1, f"expected 1 smtp event, got {smtp_total}"


def test_snapshot_throughput_has_289_buckets(db: Path):
    """24h / 5min + 1 = 289 points (inclusive start and end)."""
    snap = compute_dashboard_snapshot(db)
    tp = snap["throughput_24h"]
    for s in tp["series"]:
        assert len(s["points"]) == 289, f"{s['name']} has {len(s['points'])} points, expected 289"


# ──────────────────────────────────────────────────────────
# 3. Method breakdown
# ──────────────────────────────────────────────────────────


def test_snapshot_method_breakdown_only_counts_kindled(db: Path):
    """method_breakdown_24h keys must always be present (even if zero)."""
    snap = compute_dashboard_snapshot(db)
    mb = snap["method_breakdown_24h"]
    assert "stk" in mb
    assert "smtp" in mb
    assert isinstance(mb["stk"], int)
    assert isinstance(mb["smtp"], int)


def test_snapshot_method_breakdown_empty_db(db: Path):
    snap = compute_dashboard_snapshot(db)
    assert snap["method_breakdown_24h"] == {"stk": 0, "smtp": 0}


def test_snapshot_method_breakdown_excludes_non_kindled(db: Path):
    """Books with status='sent' (not 'kindled') must not be counted."""
    with connect(db) as conn:
        # status=sent should be excluded even with sent_method set
        _seed_book(conn, source="goodreads", status="sent", sent_method="stk", sent_at=None)

    snap = compute_dashboard_snapshot(db)
    # The 'sent' book has no sent_at, and status != 'kindled', so stk=0
    assert snap["method_breakdown_24h"]["stk"] == 0


# ──────────────────────────────────────────────────────────
# 4. Source funnel
# ──────────────────────────────────────────────────────────


def test_snapshot_source_funnel_derives_stages(db: Path):
    with connect(db) as conn:
        # goodreads: 3 discovered, 2 downloaded (file_path set), 1 sent
        _seed_book(conn, source="goodreads", status="queued", file_path=None)
        _seed_book(conn, source="goodreads", status="queued", file_path="/f/a.epub")
        _seed_book(conn, source="goodreads", status="sent", file_path="/f/b.epub")
        # kindlebangla: 2 discovered, 1 downloaded, 1 kindled
        _seed_book(conn, source="kindlebangla", status="queued", file_path=None)
        _seed_book(conn, source="kindlebangla", status="kindled", file_path="/f/c.epub")

    snap = compute_dashboard_snapshot(db)
    funnel = {s["source"]: s for s in snap["source_funnel"]}

    gr = funnel["goodreads"]
    assert gr["discovered"] == 3
    assert gr["downloaded"] == 2
    assert gr["sent"] == 1

    kb = funnel["kindlebangla"]
    assert kb["discovered"] == 2
    assert kb["downloaded"] == 1
    assert kb["sent"] == 1


# ============ window_hours param (PR #27) ============


def test_snapshot_accepts_window_hours_24(db: Path):
    """Default window: 24h with 5-min buckets => 289 labels (inclusive)."""
    snap = compute_dashboard_snapshot(db, window_hours=24)
    assert snap["window_hours"] == 24
    assert snap["throughput"]["bucket_minutes"] == 5
    assert len(snap["throughput"]["series"][0]["points"]) == 289


def test_snapshot_accepts_window_hours_168_uses_30min_buckets(db: Path):
    snap = compute_dashboard_snapshot(db, window_hours=168)
    assert snap["window_hours"] == 168
    assert snap["throughput"]["bucket_minutes"] == 30
    assert len(snap["throughput"]["series"][0]["points"]) == (168 * 60 // 30) + 1


def test_snapshot_accepts_window_hours_720_uses_2h_buckets(db: Path):
    snap = compute_dashboard_snapshot(db, window_hours=720)
    assert snap["window_hours"] == 720
    assert snap["throughput"]["bucket_minutes"] == 120
    assert len(snap["throughput"]["series"][0]["points"]) == (720 * 60 // 120) + 1


def test_snapshot_invalid_window_falls_back_to_24h(db: Path):
    snap = compute_dashboard_snapshot(db, window_hours=999)
    assert snap["window_hours"] == 24


# ============ kpis block (PR #27) ============


def test_kpis_queue_depth_counts_queued_books(db: Path):
    with connect(db) as conn:
        _seed_book(conn, status="queued")
        _seed_book(conn, status="queued")
        _seed_book(conn, status="kindled", sent_at="2026-06-01 00:00:00")
    snap = compute_dashboard_snapshot(db)
    assert snap["kpis"]["queue_depth"] == 2


def test_kpis_in_flight_counts_searching_through_sending(db: Path):
    with connect(db) as conn:
        for s in ("searching", "downloading", "converting", "sending", "queued", "kindled"):
            _seed_book(conn, status=s)
    snap = compute_dashboard_snapshot(db)
    # 4 in-flight: searching + downloading + converting + sending
    assert snap["kpis"]["in_flight"] == 4


def test_kpis_today_sent_counts_books_sent_today_utc(db: Path):
    from datetime import datetime, timedelta
    today = datetime.now(UTC).replace(hour=12, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    with connect(db) as conn:
        _seed_book(conn, status="kindled", sent_method="stk", sent_at=today)
        _seed_book(conn, status="kindled", sent_method="stk", sent_at=today)
        _seed_book(conn, status="kindled", sent_method="stk", sent_at=yesterday)
    snap = compute_dashboard_snapshot(db)
    assert snap["kpis"]["today_sent"] == 2


# ============ recent_events (PR #27) ============


def test_recent_events_returns_events_with_book_title(db: Path):
    with connect(db) as conn:
        _seed_book(conn, status="queued")
        # Insert an event referencing the book
        bid = conn.execute("SELECT id FROM books LIMIT 1").fetchone()["id"]
        conn.execute(
            """INSERT INTO events (book_id, kind, scraper, message, ts)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (bid, "state_change", "", "manually re-queued"),
        )
    snap = compute_dashboard_snapshot(db)
    ev = snap["recent_events"]
    assert len(ev) >= 1
    assert ev[0]["kind"] == "state_change"
    assert ev[0]["book_id"] == bid
    assert ev[0]["book_title"]  # joined from books table


def test_recent_events_caps_at_30(db: Path):
    with connect(db) as conn:
        _seed_book(conn, status="queued")
        bid = conn.execute("SELECT id FROM books LIMIT 1").fetchone()["id"]
        for i in range(40):
            conn.execute(
                """INSERT INTO events (book_id, kind, scraper, message, ts)
                   VALUES (?, 'state_change', '', ?, datetime('now'))""",
                (bid, f"msg {i}"),
            )
    snap = compute_dashboard_snapshot(db)
    assert len(snap["recent_events"]) == 30


# ============ stage_timings (PR #27) ============


def test_stage_timings_returns_zero_count_on_empty_db(db: Path):
    snap = compute_dashboard_snapshot(db)
    st = snap["stage_timings"]
    assert st["search_to_downloaded_seconds"]["count"] == 0
    assert st["search_to_downloaded_seconds"]["p50"] is None


def test_stage_timings_computes_percentiles_from_book_stamps(db: Path):
    from datetime import datetime, timedelta
    now = datetime.now(UTC)
    with connect(db) as conn:
        # 3 books with known stage durations:
        #   search->dl   = 10s, 20s, 60s
        #   dl->sent     = 5s,  15s, 100s
        for sec_dl, sec_sent in ((10, 5), (20, 15), (60, 100)):
            t_search = (now - timedelta(seconds=200))
            t_dl = t_search + timedelta(seconds=sec_dl)
            t_sent = t_dl + timedelta(seconds=sec_sent)
            conn.execute(
                """INSERT INTO books (title, source, status, searched_at, downloaded_at, sent_at)
                   VALUES (?, 'manual', 'kindled', ?, ?, ?)""",
                (
                    f"x-{sec_dl}",
                    t_search.strftime("%Y-%m-%d %H:%M:%S"),
                    t_dl.strftime("%Y-%m-%d %H:%M:%S"),
                    t_sent.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
    snap = compute_dashboard_snapshot(db)
    sd = snap["stage_timings"]["search_to_downloaded_seconds"]
    assert sd["count"] == 3
    assert sd["p50"] == 20  # middle value
    ds = snap["stage_timings"]["downloaded_to_sent_seconds"]
    assert ds["count"] == 3
    assert ds["p50"] == 15
