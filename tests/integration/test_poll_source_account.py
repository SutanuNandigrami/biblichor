"""Regression test for the source_id SQL hotfix.

PR #35's seen_source_ids feature ran:
    SELECT source_id FROM books WHERE source = ?
which crashed every production goodreads poll with
"no such column: source_id" -- the books table has per-source id
columns (goodreads_id, hardcover_id), not a generic source_id.

This test exercises poll_source_account end-to-end with a real
schema + a GoodreadsRSS source whose list_to_read accepts
seen_source_ids, and confirms it returns without raising.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from endless_library.config import Config
from endless_library.db.schema import init_db
from endless_library.pipeline import PipelineDeps, poll_source_account


def test_poll_source_account_seen_source_ids_uses_goodreads_id_column(
    tmp_path: Path,
):
    db_path = tmp_path / "library.db"
    init_db(db_path)
    cfg = Config()
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)

    # Insert one already-tracked goodreads book so the seen_source_ids
    # set is non-empty -- exercises the SELECT path.
    deps.books.upsert(
        title="Already tracked",
        author="x",
        isbn13=None,
        source="goodreads",
        source_id="199534613",
    )
    acct_id = deps.sources.add(
        source="goodreads",
        identifier="69278726:to-read",
        token=None,
    )

    # Patch the GoodreadsRSS factory so we don't hit the network. The
    # patched callable accepts seen_source_ids exactly the way the real
    # source does (since the pipeline introspects the signature).
    captured_kwargs: dict = {}

    class _FakeSrc:
        def list_to_read(
            self,
            *,
            identifier: str,
            token: str | None = None,
            seen_source_ids: set[str] | None = None,
        ):
            captured_kwargs["identifier"] = identifier
            captured_kwargs["seen_source_ids"] = seen_source_ids
            return iter([])  # zero new books

    with patch(
        "endless_library.sources.registry.build",
        return_value=_FakeSrc(),
    ):
        # Must not raise. Pre-hotfix this crashed with
        # sqlite3.OperationalError: no such column: source_id.
        added = poll_source_account(deps, acct_id)

    assert added == 0  # zero new books returned by fake src
    # And the SELECT projected goodreads_id, so the already-tracked id
    # must show up in the kwarg.
    assert captured_kwargs["seen_source_ids"] == {"199534613"}


def test_poll_source_account_hardcover_uses_hardcover_id_column(
    tmp_path: Path,
):
    """Symmetric coverage for the other source column."""
    db_path = tmp_path / "library.db"
    init_db(db_path)
    cfg = Config()
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    deps.books.upsert(
        title="Tracked HC",
        author=None,
        isbn13=None,
        source="hardcover",
        source_id="hc-7",
    )
    acct_id = deps.sources.add(
        source="hardcover",
        identifier="any",
        token="t",
    )
    captured: dict = {}

    class _FakeSrc:
        def list_to_read(
            self,
            *,
            identifier: str,
            token: str | None = None,
            seen_source_ids: set[str] | None = None,
        ):
            captured["seen"] = seen_source_ids
            return iter([])

    with patch(
        "endless_library.sources.registry.build",
        return_value=_FakeSrc(),
    ):
        added = poll_source_account(deps, acct_id)

    assert added == 0
    assert captured["seen"] == {"hc-7"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
