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
