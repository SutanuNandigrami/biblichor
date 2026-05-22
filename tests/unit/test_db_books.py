from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.db.books import BookRepo
from endless_library.db.schema import init_db


@pytest.fixture
def repo(tmp_path: Path) -> BookRepo:
    db = tmp_path / "library.db"
    init_db(db)
    return BookRepo(db)


def test_insert_and_get(repo: BookRepo) -> None:
    bid = repo.upsert(
        title="The Pragmatic Programmer",
        author="Hunt, Thomas",
        isbn13="9780135957059",
        source="goodreads",
        source_id="rev123",
    )
    row = repo.get(bid)
    assert row is not None
    assert row.title == "The Pragmatic Programmer"
    assert row.status == "queued"
    assert row.attempts == 0


def test_upsert_is_idempotent_per_source(repo: BookRepo) -> None:
    a = repo.upsert(title="X", author="Y", isbn13=None, source="goodreads", source_id="r1")
    b = repo.upsert(title="X", author="Y", isbn13=None, source="goodreads", source_id="r1")
    assert a == b


def test_set_status_updates_timestamp(repo: BookRepo) -> None:
    bid = repo.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    repo.set_status(bid, "searching")
    row = repo.get(bid)
    assert row is not None
    assert row.status == "searching"


def test_increment_attempts(repo: BookRepo) -> None:
    bid = repo.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    repo.increment_attempts(bid)
    repo.increment_attempts(bid)
    row = repo.get(bid)
    assert row is not None
    assert row.attempts == 2


def test_dedup_by_normalized_isbn(repo: BookRepo) -> None:
    repo.upsert(title="X", author="Y", isbn13="9780135957059", source="goodreads", source_id="r1")
    bid2 = repo.upsert(
        title="X", author="Y", isbn13="9780135957059", source="hardcover", source_id="hc1"
    )
    assert repo.count() == 1
    row = repo.find_by_isbn("9780135957059")
    assert row is not None
    assert bid2 == row.id


def test_pending_excludes_terminal_states(repo: BookRepo) -> None:
    b1 = repo.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m1")
    b2 = repo.upsert(title="B", author=None, isbn13=None, source="manual", source_id="m2")
    # Status transition: queued -> searching -> downloading -> sending -> sent
    repo.set_status(b2, "searching")
    repo.set_status(b2, "downloading")
    repo.set_status(b2, "sending")
    repo.set_status(b2, "sent")
    pending_ids = [r.id for r in repo.pending(max_attempts=5)]
    assert pending_ids == [b1]


def test_zombie_sweep(repo: BookRepo) -> None:
    bid = repo.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m1")
    repo.set_status(bid, "searching")
    repo.set_status(bid, "downloading")
    # Force updated_at into the past
    with repo._connect() as conn:
        conn.execute("UPDATE books SET updated_at = datetime('now','-1 hour') WHERE id=?", (bid,))
    repo.reset_zombies(stale_minutes=30)
    row = repo.get(bid)
    assert row is not None
    assert row.status == "queued"  # Phase 6u.5b: zombie sweep now returns books to queued so resume path picks them up


def test_mark_stage_sets_timestamp(repo: BookRepo) -> None:
    bid = repo.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    repo.mark_stage(bid, "downloaded")
    row = repo.get(bid)
    assert row is not None
    assert row.downloaded_at is not None
    assert row.converted_at is None


def test_clear_stages_from_downloaded(repo: BookRepo) -> None:
    bid = repo.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    for stage in ("searched", "downloaded", "converted", "sent"):
        repo.mark_stage(bid, stage)
    repo.clear_stages_from(bid, stage="downloaded")
    row = repo.get(bid)
    assert row is not None
    assert row.searched_at is not None
    assert row.downloaded_at is None
    assert row.converted_at is None
    assert row.sent_at is None


def test_set_status_records_file_size(repo: BookRepo) -> None:
    bid = repo.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    repo.set_status(bid, "searching")
    repo.set_status(bid, "downloading", file_size=12345)
    with repo._connect() as conn:
        r = conn.execute("SELECT file_size FROM books WHERE id=?", (bid,)).fetchone()
    assert r["file_size"] == 12345


def test_set_status_downloading_marks_downloaded_at(repo: BookRepo) -> None:
    bid = repo.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    repo.set_status(bid, "searching")
    repo.set_status(bid, "downloading", file_path="/tmp/x.epub", md5="a" * 32)
    row = repo.get(bid)
    assert row is not None
    assert row.downloaded_at is not None
