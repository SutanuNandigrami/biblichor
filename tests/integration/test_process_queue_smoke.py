"""Smoke test: process_queue with a real PipelineDeps instance.

Catches issues like slots=True on PipelineDeps where ad-hoc attribute
assignment would crash production but not the mock-deps unit tests.
"""

from pathlib import Path

from endless_library.config import Config
from endless_library.pipeline import PipelineDeps, process_queue


def test_process_queue_runs_against_real_deps_build(tmp_path: Path):
    """process_queue must work against deps from PipelineDeps.build(), not just mocks.

    Specifically catches AttributeError from slots=True dataclasses where
    code paths assign ad-hoc attributes (e.g. _batch_delivery_mode).
    """
    cfg = Config()  # defaults
    cfg.general.books_dir = str(tmp_path / "books")
    Path(cfg.general.books_dir).mkdir(parents=True)
    db_path = tmp_path / "library.db"

    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)

    # Empty queue: should complete without error, return a tally dict.
    tally = process_queue(deps)

    assert isinstance(tally, dict)
    # No books in DB -> all counters zero.
    assert all(v == 0 for v in tally.values())
