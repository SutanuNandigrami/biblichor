from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.db.books import BookRepo
from endless_library.db.candidates import CandidateRepo
from endless_library.db.schema import init_db


@pytest.fixture
def repos(tmp_path: Path) -> tuple[BookRepo, CandidateRepo]:
    db = tmp_path / "library.db"
    init_db(db)
    books = BookRepo(db)
    cands = CandidateRepo(db)
    return books, cands


def test_insert_and_top(repos: tuple[BookRepo, CandidateRepo]) -> None:
    books, cands = repos
    bid = books.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    cands.insert(
        book_id=bid,
        provider="annas",
        md5="a" * 32,
        title="X",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=1_000_000,
        year=2024,
        publisher=None,
        edition_hints="",
        score=82.0,
        detail_url="https://annas-archive.gl/md5/" + "a" * 32,
        raw_json="{}",
    )
    cands.insert(
        book_id=bid,
        provider="annas",
        md5="b" * 32,
        title="X (older)",
        author=None,
        language="en",
        format="pdf",
        filesize_bytes=2_000_000,
        year=2010,
        publisher=None,
        edition_hints="scan",
        score=55.0,
        detail_url="https://annas-archive.gl/md5/" + "b" * 32,
        raw_json="{}",
    )
    top = cands.top_for_book(bid, limit=5)
    assert [c.md5 for c in top] == ["a" * 32, "b" * 32]
    assert top[0].score == 82.0


def test_clear_for_book(repos: tuple[BookRepo, CandidateRepo]) -> None:
    books, cands = repos
    bid = books.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    cands.insert(
        book_id=bid,
        provider="annas",
        md5=None,
        title="X",
        author=None,
        language=None,
        format=None,
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints=None,
        score=10.0,
        detail_url="u",
        raw_json="{}",
    )
    cands.clear_for_book(bid)
    assert cands.top_for_book(bid, limit=5) == []
