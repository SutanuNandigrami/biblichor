from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.db.schema import init_db
from endless_library.db.sources import SourceAccountRepo


@pytest.fixture
def repo(tmp_path: Path) -> SourceAccountRepo:
    db = tmp_path / "library.db"
    init_db(db)
    return SourceAccountRepo(db)


def test_add_list_toggle(repo: SourceAccountRepo) -> None:
    aid = repo.add(source="goodreads", identifier="123:to-read", token=None)
    rows = repo.list_all()
    assert len(rows) == 1 and rows[0].enabled is True
    repo.set_enabled(aid, False)
    assert repo.list_all()[0].enabled is False


def test_mark_polled(repo: SourceAccountRepo) -> None:
    aid = repo.add(source="hardcover", identifier="me", token="t")
    repo.mark_polled(aid)
    row = repo.get(aid)
    assert row is not None
    assert row.last_polled_at is not None


def test_unique_source_identifier(repo: SourceAccountRepo) -> None:
    repo.add(source="goodreads", identifier="123:to-read", token=None)
    with pytest.raises(__import__("sqlite3").IntegrityError):
        repo.add(source="goodreads", identifier="123:to-read", token=None)
