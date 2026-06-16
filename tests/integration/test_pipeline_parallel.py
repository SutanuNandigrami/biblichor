"""Tests for the pipeline-level parallel book processing.

PR #46 adds general.parallel_books. When 1 (default), process_queue
runs serially in the current process -- pre-PR behaviour. When > 1, it
fans out to ProcessPoolExecutor with that many workers.

The atomic claim_for_processing inside process_one keeps two workers
from racing the same book. These tests exercise the dispatch layer
(`_run_books`), not the actual book-processing internals.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from endless_library.config import Config
from endless_library.db.books import BookRow
from endless_library.db.schema import init_db
from endless_library.pipeline import PipelineDeps, _run_books


def _book(id_: int) -> BookRow:
    """Minimal BookRow stand-in for the dispatch layer (which only
    reads .id off the rows it receives)."""
    return BookRow(
        id=id_,
        title=f"book-{id_}",
        author=None,
        isbn13=None,
        goodreads_id=None,
        hardcover_id=None,
        source="manual",
        status="queued",
        format=None,
        file_path=None,
        md5=None,
        picked_candidate_id=None,
        attempts=0,
        last_error=None,
        created_at="2026-06-17 00:00:00",
        updated_at="2026-06-17 00:00:00",
        searched_at=None,
        downloaded_at=None,
        converted_at=None,
        sent_at=None,
        series=None,
        tags=None,
    )


def _deps(tmp_path: Path, parallel: int = 1) -> PipelineDeps:
    db = tmp_path / "library.db"
    init_db(db)
    cfg = Config()
    cfg.general.parallel_books = parallel
    return PipelineDeps.build(cfg=cfg, db_path=db)


def test_run_books_serial_calls_process_one_in_order(tmp_path: Path):
    deps = _deps(tmp_path, parallel=1)
    books = [_book(1), _book(2), _book(3)]
    seen_ids: list[int] = []

    def _fake_process_one(_deps, b):
        seen_ids.append(b.id)
        return "sent"

    with patch("endless_library.pipeline.process_one", _fake_process_one):
        out = _run_books(deps, books, batch_mode=False)

    assert seen_ids == [1, 2, 3]
    assert [st for _b, st in out] == ["sent", "sent", "sent"]


def test_run_books_serial_translates_crashes_to_failed(tmp_path: Path):
    """process_one crashing on book B must mark B as failed in the
    tally + persist B as failed in the DB so the next tick doesn't
    re-attempt without bound."""
    deps = _deps(tmp_path, parallel=1)
    # Insert a real book row so set_failed has something to update.
    bid = deps.books.upsert(
        title="boom",
        author=None,
        isbn13=None,
        source="manual",
        source_id="m-boom",
    )
    book = deps.books.get(bid)
    assert book is not None

    def _crash(_deps, _b):
        raise RuntimeError("kaboom")

    with patch("endless_library.pipeline.process_one", _crash):
        out = _run_books(deps, [book], batch_mode=False)

    assert out == [(book, "failed")]
    assert deps.books.get(bid).status == "failed"


def test_run_books_single_book_stays_serial(tmp_path: Path):
    """Even when parallel_books > 1, a single book is processed
    serially -- spinning up a subprocess pool for one book is pure
    overhead. Avoids a startup tax on light ticks."""
    deps = _deps(tmp_path, parallel=4)
    book = _book(7)
    called: list[int] = []

    def _fake_process_one(_deps, b):
        called.append(b.id)
        return "sent"

    with patch("endless_library.pipeline.process_one", _fake_process_one), patch(
        "endless_library.pipeline.ProcessPoolExecutor"
    ) as ppe:
        out = _run_books(deps, [book], batch_mode=False)

    ppe.assert_not_called()  # Pool MUST NOT be constructed for 1 book.
    assert called == [7]
    assert [st for _b, st in out] == ["sent"]


def test_run_books_serial_path_when_parallel_is_one(tmp_path: Path):
    """parallel_books=1 must never touch ProcessPoolExecutor at all,
    matching pre-PR behaviour exactly."""
    deps = _deps(tmp_path, parallel=1)
    books = [_book(1), _book(2)]

    with patch(
        "endless_library.pipeline.process_one", lambda *_a: "sent"
    ), patch("endless_library.pipeline.ProcessPoolExecutor") as ppe:
        _run_books(deps, books, batch_mode=False)

    ppe.assert_not_called()


def test_run_books_parallel_uses_process_pool(tmp_path: Path):
    """When parallel_books > 1 AND len(books) > 1, dispatch goes
    through ProcessPoolExecutor."""
    deps = _deps(tmp_path, parallel=2)
    books = [_book(1), _book(2), _book(3)]

    # Patch the worker fn at the symbol the parent submits, and the
    # ProcessPoolExecutor itself, so we can verify dispatch shape
    # without actually forking.
    from unittest.mock import MagicMock

    fake_ex = MagicMock()
    fake_ex.__enter__ = MagicMock(return_value=fake_ex)
    fake_ex.__exit__ = MagicMock(return_value=False)
    submitted_book_ids: list[int] = []

    def _submit(fn, book_id, _cfg_json, _db_path_str, _batch_mode):
        submitted_book_ids.append(book_id)
        fut = MagicMock()
        fut.result.return_value = "sent"
        return fut

    fake_ex.submit.side_effect = _submit

    def _as_completed(futures):
        # MagicMocks; just yield them all in insertion order.
        return list(futures)

    with patch(
        "endless_library.pipeline.ProcessPoolExecutor", return_value=fake_ex
    ), patch("endless_library.pipeline.as_completed", _as_completed):
        out = _run_books(deps, books, batch_mode=False)

    assert sorted(submitted_book_ids) == [1, 2, 3]
    assert [st for _b, st in out] == ["sent", "sent", "sent"]


def test_run_books_parallel_translates_worker_exception_to_failed(
    tmp_path: Path,
):
    """A worker subprocess that explodes on result() must be tallied as
    'failed' so the parent's accounting stays consistent."""
    deps = _deps(tmp_path, parallel=2)
    books = [_book(1), _book(2)]

    from unittest.mock import MagicMock

    fake_ex = MagicMock()
    fake_ex.__enter__ = MagicMock(return_value=fake_ex)
    fake_ex.__exit__ = MagicMock(return_value=False)

    def _submit(fn, book_id, _cfg_json, _db_path_str, _batch_mode):
        fut = MagicMock()
        if book_id == 1:
            fut.result.side_effect = RuntimeError("worker died")
        else:
            fut.result.return_value = "sent"
        return fut

    fake_ex.submit.side_effect = _submit

    with patch(
        "endless_library.pipeline.ProcessPoolExecutor", return_value=fake_ex
    ), patch("endless_library.pipeline.as_completed", lambda fs: list(fs)):
        out = _run_books(deps, books, batch_mode=False)

    statuses = sorted(st for _b, st in out)
    assert statuses == ["failed", "sent"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
