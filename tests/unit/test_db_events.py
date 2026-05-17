from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.db.books import BookRepo
from endless_library.db.events import EventRepo
from endless_library.db.schema import init_db


@pytest.fixture
def repos(tmp_path: Path) -> tuple[BookRepo, EventRepo]:
    db = tmp_path / "library.db"
    init_db(db)
    return BookRepo(db), EventRepo(db)


def test_append_and_recent(repos: tuple[BookRepo, EventRepo]) -> None:
    books, events = repos
    bid = books.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    events.append(book_id=bid, kind="state_change", message="queued -> searching")
    events.append(book_id=bid, kind="scrape", scraper="annas_curl", message="hit", meta={"hits": 3})
    recent = events.recent_for_book(bid)
    assert len(recent) == 2
    assert recent[0].kind == "scrape"  # newest first
    assert recent[0].meta == {"hits": 3}


def test_recent_global_limit(repos: tuple[BookRepo, EventRepo]) -> None:
    books, events = repos
    bid = books.upsert(title="X", author=None, isbn13=None, source="manual", source_id="m1")
    for i in range(10):
        events.append(book_id=bid, kind="state_change", message=str(i))
    rows = events.recent_global(limit=3)
    assert [r.message for r in rows] == ["9", "8", "7"]
