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

    def _as_completed(futures, **_kw):
        # MagicMocks; just yield them all in insertion order. The
        # `**_kw` absorbs the `timeout=...` kwarg the production
        # path passes.
        return list(futures)

    with patch(
        "endless_library.pipeline.ProcessPoolExecutor", return_value=fake_ex
    ), patch("endless_library.pipeline.as_completed", _as_completed):
        out = _run_books(deps, books, batch_mode=False)

    assert sorted(submitted_book_ids) == [1, 2, 3]
    assert [st for _b, st in out] == ["sent", "sent", "sent"]


def test_run_books_parallel_translates_tick_timeout_to_failed(tmp_path: Path):
    """When as_completed raises TimeoutError (the whole tick budget
    ran out before all workers returned), unfinished futures must be
    explicitly marked 'failed' so the queue keeps moving. Hotfix for
    the 2026-06-17 hang where a stuck patchright blocked the tick
    indefinitely, which in turn blocked zombie reset on the next tick."""
    from concurrent.futures import TimeoutError as _CFTimeoutError
    from unittest.mock import MagicMock

    deps = _deps(tmp_path, parallel=2)
    # Insert real book rows so set_failed has something to write to.
    bid1 = deps.books.upsert(
        title="A", author=None, isbn13=None, source="manual", source_id="m-1"
    )
    bid2 = deps.books.upsert(
        title="B", author=None, isbn13=None, source="manual", source_id="m-2"
    )
    bid3 = deps.books.upsert(
        title="C", author=None, isbn13=None, source="manual", source_id="m-3"
    )
    book_a = deps.books.get(bid1)
    book_b = deps.books.get(bid2)
    book_c = deps.books.get(bid3)
    assert book_a and book_b and book_c

    fake_ex = MagicMock()
    fake_ex.__enter__ = MagicMock(return_value=fake_ex)
    fake_ex.__exit__ = MagicMock(return_value=False)

    # 3 futures: A completes "sent"; B and C never resolve.
    fut_a = MagicMock()
    fut_a.result.return_value = "sent"
    fut_b = MagicMock()
    fut_c = MagicMock()
    submitted = [fut_a, fut_b, fut_c]
    submit_idx = {"i": 0}

    def _submit(_fn, _book_id, *_a):
        fut = submitted[submit_idx["i"]]
        submit_idx["i"] += 1
        return fut

    fake_ex.submit.side_effect = _submit

    def _as_completed(_fs, **_kw):
        # Yield only the first future, then raise TimeoutError.
        yield fut_a
        raise _CFTimeoutError("tick budget exhausted")

    with patch(
        "endless_library.pipeline.ProcessPoolExecutor", return_value=fake_ex
    ), patch("endless_library.pipeline.as_completed", _as_completed):
        out = _run_books(deps, [book_a, book_b, book_c], batch_mode=False)

    statuses = sorted(st for _b, st in out)
    assert statuses == ["failed", "failed", "sent"]
    # B and C must have been persisted as failed
    assert deps.books.get(bid2).status == "failed"
    assert deps.books.get(bid3).status == "failed"
    # The error message must mention the timeout
    assert "tick timeout" in (deps.books.get(bid2).last_error or "").lower()


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
    ), patch("endless_library.pipeline.as_completed", lambda fs, **_kw: list(fs)):
        out = _run_books(deps, books, batch_mode=False)

    statuses = sorted(st for _b, st in out)
    assert statuses == ["failed", "sent"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
