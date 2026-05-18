"""Regression + feature-intact tests for Phase 3c auto-skip of dead-end books.

Audit context: books that no scraper can find used to cycle through
`failed` until attempts hit max_attempts. They'd then sit in `failed`
forever — invisible to the user who'd assume "the system tried and
gave up" without knowing whether to wait, retry, or remove.

The fix: after `max_search_attempts_before_skip` consecutive search-
phase failures, transition to `skipped` (a separate terminal state).
The /retry button still works — it clears state and re-queues — but
the queue UI shows skipped books distinctly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from endless_library.config import Config
from endless_library.pipeline import PipelineDeps, _search_fail_or_skip


def _make_deps(tmp_path: Path, *, skip_after: int = 3) -> PipelineDeps:
    cfg = Config()
    cfg.general.max_search_attempts_before_skip = skip_after
    cfg.scrapers.order = ["annas_curl"]
    cfg.scrapers.enabled = {"annas_curl": True}
    return PipelineDeps.build(cfg=cfg, db_path=tmp_path / "library.db")


def _book(deps: PipelineDeps, **kw):
    base = dict(title="X", author="Y", isbn13=None, source="manual", source_id=None)
    base.update(kw)
    bid = deps.books.upsert(**base)
    return deps.books.get(bid)


# ============ REGRESSION: Nth fruitless search transitions to skipped ============


def test_first_search_failure_still_marks_failed(tmp_path):
    """Default budget is 3. First failure (attempts going 0 -> 1)
    must NOT auto-skip — give the book multiple shots."""
    deps = _make_deps(tmp_path, skip_after=3)
    book = _book(deps)
    assert book.attempts == 0

    result = _search_fail_or_skip(deps, book, "no candidates")
    assert result == "failed"
    refreshed = deps.books.get(book.id)
    assert refreshed.status == "failed"
    assert refreshed.attempts == 1


def test_second_search_failure_still_marks_failed(tmp_path):
    """Second failure (attempts going 1 -> 2) is still under budget=3."""
    deps = _make_deps(tmp_path, skip_after=3)
    book = _book(deps)
    deps.books.set_failed(book.id, error="seed prior failure")
    book = deps.books.get(book.id)
    assert book.attempts == 1

    result = _search_fail_or_skip(deps, book, "no candidates")
    assert result == "failed"
    refreshed = deps.books.get(book.id)
    assert refreshed.status == "failed"
    assert refreshed.attempts == 2


def test_third_search_failure_auto_skips_with_budget_3(tmp_path):
    """Third fruitless cycle — attempts=2 going in, threshold=3.
    Now we park instead of failing again."""
    deps = _make_deps(tmp_path, skip_after=3)
    book = _book(deps)
    # Pre-load 2 prior failures
    deps.books.set_failed(book.id, error="seed 1")
    deps.books.set_failed(book.id, error="seed 2")
    book = deps.books.get(book.id)
    assert book.attempts == 2

    result = _search_fail_or_skip(deps, book, "no candidates from any scraper")
    assert result == "skipped"
    refreshed = deps.books.get(book.id)
    assert refreshed.status == "skipped"
    # Skipped does NOT bump attempts further
    assert refreshed.attempts == 2
    assert "parked after 3 attempts" in refreshed.last_error


def test_skip_threshold_is_configurable(tmp_path):
    """skip_after=1 means the very first failure parks. skip_after=10
    means books take 10 cycles to park."""
    # Aggressive (skip immediately)
    deps_aggressive = _make_deps(tmp_path / "a", skip_after=1)
    b1 = _book(deps_aggressive)
    assert _search_fail_or_skip(deps_aggressive, b1, "x") == "skipped"

    # Patient
    deps_patient = _make_deps(tmp_path / "p", skip_after=10)
    b2 = _book(deps_patient)
    for _ in range(9):
        deps_patient.books.set_failed(b2.id, error="cycle")
    b2 = deps_patient.books.get(b2.id)
    assert b2.attempts == 9
    assert _search_fail_or_skip(deps_patient, b2, "x") == "skipped"


# ============ REGRESSION: skipped is a terminal state but retry works ============


def test_skipped_state_is_legal_via_state_machine(tmp_path):
    """The book transitions searching -> skipped (Phase 3c) and that
    transition is already legal per _LEGAL (Phase 1 audit alignment)."""
    from endless_library.domain.state_machine import is_legal_transition
    assert is_legal_transition("searching", "skipped")
    # And the retry button can re-queue from skipped
    assert is_legal_transition("skipped", "queued")


# ============ FEATURE-INTACT: non-search failures still use set_failed ============


def test_set_failed_still_bumps_attempts(tmp_path):
    """The set_failed primitive is unchanged. Convert/send/etc. paths
    that call it directly continue to behave as before."""
    deps = _make_deps(tmp_path)
    book = _book(deps)
    assert book.attempts == 0
    deps.books.set_failed(book.id, error="conversion failed")
    refreshed = deps.books.get(book.id)
    assert refreshed.status == "failed"
    assert refreshed.attempts == 1


def test_set_skipped_is_attempts_neutral(tmp_path):
    """set_skipped doesn't bump attempts (the book is being parked, not
    retried). The /retry path will clear state for a fresh count."""
    deps = _make_deps(tmp_path)
    book = _book(deps)
    deps.books.set_failed(book.id, error="x")
    deps.books.set_failed(book.id, error="y")
    book = deps.books.get(book.id)
    assert book.attempts == 2

    deps.books.set_skipped(book.id, error="parked")
    refreshed = deps.books.get(book.id)
    assert refreshed.status == "skipped"
    assert refreshed.attempts == 2  # unchanged


def test_set_skipped_records_error_message(tmp_path):
    """The reason carries to the UI so the user can see why the book parked."""
    deps = _make_deps(tmp_path)
    book = _book(deps)
    deps.books.set_skipped(book.id, error="no candidates from any provider")
    refreshed = deps.books.get(book.id)
    assert refreshed.last_error == "no candidates from any provider"
