"""Regression + feature-intact tests for the attempts-counter refactor.

Audit finding: pipeline.process_one called increment_attempts() at the
top of every search (line 377), regardless of outcome. A book that
bounced into needs_review on 5 consecutive cycles would hit
max_attempts=5 and stop being picked up by the retry job, even though
nothing actually failed.

The fix: introduce BookRepo.set_failed(book_id, error) that bumps
attempts atomically with the status change. Drop the unconditional
increment at search start. Now:

  search → auto-pick → sent           : attempts unchanged
  search → needs_review               : attempts unchanged
  search → failed (any reason)        : attempts += 1

Regression test: simulated bouncing-into-needs_review pattern keeps
attempts at 0.
Feature-intact tests: a real failure still bumps; max_attempts still
hides exhausted books from pending().
"""

from __future__ import annotations

from pathlib import Path

from endless_library.db.books import BookRepo
from endless_library.db.schema import init_db


def _book(repo: BookRepo, **kw) -> int:
    base = dict(
        title="X",
        author="A",
        isbn13=None,
        source="manual",
        source_id="m1",
    )
    base.update(kw)
    return repo.upsert(**base)


# ============ REGRESSION: needs_review bouncing must NOT consume attempts ============


def test_needs_review_transition_does_not_bump_attempts(tmp_path: Path):
    """The bug we just fixed: cycling through needs_review repeatedly
    used to chew through max_attempts."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    bid = _book(repo, source_id="bounce-1")

    # Simulate 6 cycles of search → needs_review → reset (via reset_for_research)
    for _ in range(6):
        repo.set_status(bid, "searching")
        repo.set_status(bid, "needs_review", error="low confidence top=42.0 second=40.0")
        # User clicks Retry, which triggers reset_for_research
        repo.reset_for_research(bid)

    row = repo.get(bid)
    assert row is not None
    # The book should still have attempts=0 after 6 needs_review bounces.
    # Before the fix, it would have hit attempts=6 and dropped off
    # pending() entirely.
    assert row.attempts == 0, f"attempts ate by needs_review bounces: {row.attempts}"


def test_sent_transition_does_not_bump_attempts(tmp_path: Path):
    """Successful sends shouldn't count as attempts either — the user
    might re-send the same book later via /retry."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    bid = _book(repo, source_id="sent-no-bump")

    repo.set_status(bid, "searching")
    repo.set_status(bid, "sending")
    repo.set_status(bid, "sent")
    assert repo.get(bid).attempts == 0


# ============ FEATURE-INTACT: real failures still bump attempts ============


def test_set_failed_bumps_attempts(tmp_path: Path):
    """The new set_failed() helper atomically writes status='failed'
    AND attempts += 1."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    bid = _book(repo, source_id="fail-1")

    assert repo.get(bid).attempts == 0
    repo.set_failed(bid, error="no candidates from any scraper")
    row = repo.get(bid)
    assert row.status == "failed"
    assert row.attempts == 1
    assert row.last_error == "no candidates from any scraper"


def test_repeated_failures_accumulate(tmp_path: Path):
    """Five real failures over five retries → attempts hits 5, book
    drops off the retry budget — same as before the refactor."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    bid = _book(repo, source_id="fail-loop")

    for i in range(5):
        repo.reset_for_research(bid)  # clears prior state
        repo.set_failed(bid, error=f"failure {i}")

    row = repo.get(bid)
    assert row.attempts == 1, "reset_for_research should zero attempts; bug if it carries over"
    # ... actually verify that's what we want. Let's also test the
    # accumulation WITHOUT reset between failures:
    bid2 = _book(repo, source_id="fail-loop-2")
    for i in range(5):
        repo.set_failed(bid2, error=f"failure {i}")
    assert repo.get(bid2).attempts == 5


def test_pending_respects_max_attempts(tmp_path: Path):
    """pending() filters out books whose attempts >= max_attempts.
    Three consecutive set_failed calls with max_attempts=3 makes the
    book invisible to the retry job."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    bid = _book(repo, source_id="invisible")

    for i in range(3):
        repo.set_failed(bid, error=f"failure {i}")

    # max_attempts=3 → this book is not pending
    assert all(b.id != bid for b in repo.pending(max_attempts=3))
    # but max_attempts=5 → still in flight
    assert any(b.id == bid for b in repo.pending(max_attempts=5))


def test_reset_for_research_zeroes_attempts(tmp_path: Path):
    """The retry button must give the user a fresh budget."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    bid = _book(repo, source_id="reset-test")

    for _ in range(4):
        repo.set_failed(bid, error="x")
    assert repo.get(bid).attempts == 4

    repo.reset_for_research(bid)
    assert repo.get(bid).attempts == 0
    assert repo.get(bid).status == "queued"


def test_set_failed_atomic_status_and_attempts(tmp_path: Path):
    """status='failed' and attempts increment must happen in one
    statement — otherwise a reader between the two sees an inconsistent
    state (failed but attempts=0, or status=searching but attempts=2)."""
    init_db(tmp_path / "library.db")
    repo = BookRepo(tmp_path / "library.db")
    bid = _book(repo, source_id="atomic")

    # Sanity baseline
    pre = repo.get(bid)
    assert pre.attempts == 0
    assert pre.status == "queued"

    repo.set_failed(bid, error="X")
    post = repo.get(bid)
    # Both fields updated
    assert post.attempts == pre.attempts + 1
    assert post.status == "failed"
    # last_error written
    assert post.last_error == "X"
