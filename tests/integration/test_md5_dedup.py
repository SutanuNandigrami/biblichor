"""Tests for md5 post-download dedup.

PR #47: when a candidate's md5 matches a book that already has a
downloaded file on disk, mark the current row as 'skipped' (duplicate
of #N) and short-circuit the download. Stops actual wasted downloads
when the same physical book lands in the queue via multiple paths
(e.g. on 3 different goodreads sources without ISBN).
"""

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


# ============ BookRepo.find_downloaded_by_md5 ============


def test_find_downloaded_by_md5_returns_existing(repo: BookRepo):
    bid = repo.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m-a")
    repo.set_status(bid, "downloading", md5="abc" * 10 + "ab", file_path="/data/books/A.epub")
    found = repo.find_downloaded_by_md5("abc" * 10 + "ab")
    assert found is not None
    assert found.id == bid


def test_find_downloaded_by_md5_excludes_self(repo: BookRepo):
    bid = repo.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m-a")
    md5 = "f" * 32
    repo.set_status(bid, "downloading", md5=md5, file_path="/data/books/A.epub")
    # Excluding ourselves -> no match
    assert repo.find_downloaded_by_md5(md5, exclude_book_id=bid) is None
    # Not excluding -> match
    assert repo.find_downloaded_by_md5(md5) is not None


def test_find_downloaded_by_md5_returns_oldest(repo: BookRepo):
    """When 2+ rows have the same md5, the oldest (lowest id) wins."""
    md5 = "c" * 32
    bid1 = repo.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m-1")
    bid2 = repo.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m-2")
    repo.set_status(bid1, "downloading", md5=md5, file_path="/p/1.epub")
    repo.set_status(bid2, "downloading", md5=md5, file_path="/p/2.epub")
    found = repo.find_downloaded_by_md5(md5)
    assert found is not None
    assert found.id == bid1


def test_find_downloaded_by_md5_skips_rows_without_file_path(repo: BookRepo):
    """A row with md5 but no file_path isn't yet downloaded -- don't
    treat it as a dedup target."""
    md5 = "d" * 32
    bid = repo.upsert(title="A", author=None, isbn13=None, source="manual", source_id="m-a")
    repo.set_status(bid, "downloading", md5=md5)  # no file_path
    assert repo.find_downloaded_by_md5(md5) is None


def test_find_downloaded_by_md5_returns_none_when_no_match(repo: BookRepo):
    assert repo.find_downloaded_by_md5("never-seen-md5") is None


# ============ BookRepo.mark_dedup ============


def test_mark_dedup_sets_skipped_status_and_message(repo: BookRepo):
    md5 = "e" * 32
    bid_orig = repo.upsert(
        title="Orig", author=None, isbn13=None, source="manual", source_id="m-orig"
    )
    bid_dup = repo.upsert(title="Dup", author=None, isbn13=None, source="manual", source_id="m-dup")
    repo.mark_dedup(bid_dup, bid_orig, md5)
    row = repo.get(bid_dup)
    assert row is not None
    assert row.status == "skipped"
    assert row.md5 == md5
    assert f"duplicate of book #{bid_orig}" in (row.last_error or "")


def test_mark_dedup_leaves_other_books_alone(repo: BookRepo):
    md5 = "1" * 32
    bid_orig = repo.upsert(
        title="Orig", author=None, isbn13=None, source="manual", source_id="m-orig"
    )
    bid_dup = repo.upsert(title="Dup", author=None, isbn13=None, source="manual", source_id="m-dup")
    repo.mark_dedup(bid_dup, bid_orig, md5)
    orig = repo.get(bid_orig)
    assert orig is not None
    assert orig.status != "skipped"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
