"""Regression: manual-pick honored, not silently re-searched.

Covers the 2026-05-25 bug: 'the master and its emissary' (book #1311 in
prod) bounced between picked and needs_review forever because process_one
re-searched on every cycle and the top score was tied (51/51), routing back
to needs_review each time.

Fix: process_one now checks book.picked_candidate_id before doing any search.
If set, it reconstructs the Candidate from the stored CandidateRow and calls
_resolve_and_download directly, bypassing the search/score/auto-pick flow.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from endless_library.db.books import BookRepo
from endless_library.db.candidates import CandidateRepo, CandidateRow
from endless_library.db.schema import init_db
from endless_library.pipeline import PipelineDeps, process_one


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_deps(tmp_path: Path) -> PipelineDeps:
    from endless_library.config import Config
    cfg = Config()
    cfg.scrapers.order = ["annas_curl"]
    cfg.scrapers.enabled = {"annas_curl": True}
    return PipelineDeps.build(cfg=cfg, db_path=tmp_path / "library.db")


def _seed_book(deps: PipelineDeps, *, picked_candidate_id: int | None = None) -> object:
    """Insert a book in needs_review status and return a BookRow for it."""
    bid = deps.books.upsert(
        title="The Master and His Emissary",
        author="Iain McGilchrist",
        isbn13=None,
        source="manual",
        source_id="test:mcgilchrist",
    )
    deps.books.set_status(bid, "needs_review", error="low confidence top=51.0 second=51.0")
    if picked_candidate_id is not None:
        conn = sqlite3.connect(deps.books.db_path)
        conn.execute(
            "UPDATE books SET picked_candidate_id = ? WHERE id = ?",
            (picked_candidate_id, bid),
        )
        conn.commit()
        conn.close()
    return deps.books.get(bid)


def _seed_candidate(deps: PipelineDeps, book_id: int, cand_id_hint: int | None = None) -> CandidateRow:
    """Insert a candidate row and return it."""
    inserted_id = deps.cands.insert(
        book_id=book_id,
        provider="annas",
        md5="abc123",
        title="The Master and His Emissary",
        author="Iain McGilchrist",
        language="en",
        format="epub",
        filesize_bytes=5_000_000,
        year=2009,
        publisher="Yale University Press",
        edition_hints="",
        score=51.0,
        detail_url="https://annas-archive.org/md5/abc123",
        raw_json='{"isbns": []}',
    )
    return deps.cands.get_by_id(inserted_id)


# ---------------------------------------------------------------------------
# Test 1: manual pick honored — search must NOT run
# ---------------------------------------------------------------------------

def test_process_one_honors_manual_pick(tmp_path: Path) -> None:
    """When book.picked_candidate_id is set, process_one must call
    _resolve_and_download with that candidate WITHOUT running search.

    Regression for 2026-05-25 bug.
    """
    deps = _make_deps(tmp_path)

    # Insert a real candidate row we can reference
    # First insert the book to get its id
    bid = deps.books.upsert(
        title="The Master and His Emissary",
        author="Iain McGilchrist",
        isbn13=None,
        source="manual",
        source_id="test:mcgilchrist:1",
    )
    cand = _seed_candidate(deps, bid)
    # Now set the book to needs_review with the picked candidate
    deps.books.set_status(bid, "needs_review", error="low confidence top=51.0 second=51.0")
    conn = sqlite3.connect(str(deps.books.db_path))
    conn.execute("UPDATE books SET picked_candidate_id = ? WHERE id = ?", (cand.id, bid))
    conn.commit()
    conn.close()
    book = deps.books.get(bid)

    fake_file = tmp_path / "mcgilchrist.epub"
    fake_file.write_bytes(b"fake epub content")

    with patch(
        "endless_library.pipeline._resolve_and_download",
        return_value=(fake_file, None),
    ) as mock_resolve, patch(
        "endless_library.pipeline._process_from_downloaded",
        return_value="sent",
    ) as mock_process, patch(
        "endless_library.pipeline._search_with_strategies",
        side_effect=AssertionError("search must NOT run when manual pick is set"),
    ):
        result = process_one(deps, book)

    assert result == "sent", f"expected sent, got {result!r}"
    mock_resolve.assert_called_once()
    # The candidate passed to _resolve_and_download must be built from
    # the picked candidate row (same detail_url, md5, etc.)
    _, call_args, _ = mock_resolve.mock_calls[0]
    passed_cand = call_args[2]  # third positional arg: c
    assert passed_cand.detail_url == cand.detail_url
    assert passed_cand.md5 == cand.md5
    mock_process.assert_called_once()

    # Verify the honoring event was logged
    events_db = deps.events.recent_for_book(bid)
    messages = [e.message for e in events_db]
    assert any(
        "honoring manual pick" in (m or "") and str(cand.id) in (m or "")
        for m in messages
    ), f"honoring event not found in: {messages}"


# ---------------------------------------------------------------------------
# Test 2: picked candidate pruned — must fall through to fresh search
# ---------------------------------------------------------------------------

def test_process_one_falls_through_when_picked_candidate_missing(tmp_path: Path) -> None:
    """If picked_candidate_id points to a row that no longer exists
    (e.g., pruned after a clear_for_book), process_one falls through to
    a fresh search rather than crashing."""
    deps = _make_deps(tmp_path)

    bid = deps.books.upsert(
        title="Ghost Book",
        author="Nobody",
        isbn13=None,
        source="manual",
        source_id="test:ghost:1",
    )
    # picked_candidate_id=99999 — this ID does not exist in candidates
    deps.books.set_status(bid, "needs_review", error="low confidence")
    conn = sqlite3.connect(str(deps.books.db_path))
    conn.execute("UPDATE books SET picked_candidate_id = 99999 WHERE id = ?", (bid,))
    conn.commit()
    conn.close()
    book = deps.books.get(bid)

    search_called = []

    def fake_search(d, b):
        search_called.append(True)
        return [], None  # no candidates — triggers _search_fail_or_skip

    with patch(
        "endless_library.pipeline._search_with_strategies",
        side_effect=fake_search,
    ):
        result = process_one(deps, book)

    assert search_called, "search must run when picked candidate is missing"
    # result is "failed" or "skipped" (not "sent") because search returned no candidates
    assert result in ("failed", "skipped"), f"unexpected result: {result!r}"

    # Verify the fallback event was logged
    events_db = deps.events.recent_for_book(bid)
    messages = [e.message for e in events_db]
    assert any(
        "not found" in (m or "") and "re-searching" in (m or "")
        for m in messages
    ), f"fallback event not found in: {messages}"
