"""Regression tests for oversize-SMTP routing to STK.

Before this change, _process_from_downloaded unconditionally marked a
book "needs_review" when the file exceeded the SMTP attachment cap.
With STK live, oversize files should fall through to STK delivery
(which has no SMTP-size constraint) instead of stranding the book
behind a manual-review wall.

Audit: 4 of the 5 most recent needs_review rows on prod (book ids
692, 693, 791, 810) all had last_error "too large for SMTP: NNMB raw
-> ..." despite STK being fully configured. STK could have delivered
them.

Two tests:
  1. STK configured + oversize file: do NOT bounce to needs_review,
     log the "oversize-routed-stk" event instead, hand off to delivery.
  2. STK not configured + oversize file: keep the original bounce
     (the file is genuinely undeliverable; manual review is correct).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from endless_library.config import Config
from endless_library.pipeline import PipelineDeps, _process_from_downloaded

# A raw size that is comfortably oversize: 30 MB raw -> 42 MB inflated,
# vs default 24 MB cap. The 1.4 inflation factor matches pipeline.py.
OVERSIZE_RAW_BYTES = 30 * 1024 * 1024


# --- helpers --------------------------------------------------------------

def _make_deps(tmp_path: Path) -> PipelineDeps:
    cfg = Config()
    cfg.scrapers.order = ["annas_curl"]
    cfg.scrapers.enabled = {"annas_curl": True}
    # Disable optional pipeline branches we do not want firing during this test.
    cfg.calibre.enabled = False
    cfg.bookorbit.enabled = False
    return PipelineDeps.build(cfg=cfg, db_path=tmp_path / "library.db")


def _make_oversize_epub(tmp_path: Path) -> Path:
    """Create a file just above the SMTP cap so the size guard fires."""
    p = tmp_path / "huge.epub"
    p.write_bytes(b"\x00" * OVERSIZE_RAW_BYTES)
    return p


def _seed_book(deps: PipelineDeps) -> object:
    bid = deps.books.upsert(
        title="A Big Book",
        author="Some Author",
        isbn13=None,
        source="manual",
        source_id="test:oversize:1",
    )
    deps.books.set_status(bid, "downloading", file_path="/tmp/placeholder", format="epub")
    return deps.books.get(bid)


# --- tests ----------------------------------------------------------------

def test_oversize_with_stk_configured_does_not_bounce_to_needs_review(tmp_path):
    """When STK is configured and the file is too large for SMTP, the
    pipeline must NOT mark the book needs_review. It should log an
    "oversize-routed-stk" event and hand the file off to the delivery
    router (which will use STK).
    """
    deps = _make_deps(tmp_path)
    book = _seed_book(deps)
    big_file = _make_oversize_epub(tmp_path)

    # Pretend STK is configured.
    with patch(
        "endless_library.kindle_stk.KindleStkService.is_configured",
        return_value=True,
    ), patch(
        "endless_library.pipeline._kindle_deliver",
    ) as mock_deliver:
        # Simulate a successful STK delivery so the function returns "sent".
        from endless_library.kindle_router import DeliveryMethod, DeliveryResult
        mock_deliver.return_value = DeliveryResult(
            ok=True,
            method=DeliveryMethod.STK,
            error=None,
            attempts=1,
            duration_ms=10,
        )
        result = _process_from_downloaded(deps, book, big_file)

    assert result == "sent", f"expected sent (STK delivery), got {result!r}"

    # Status MUST NOT be needs_review.
    refreshed = deps.books.get(book.id)
    assert refreshed.status != "needs_review", (
        f"book bounced to needs_review despite STK being configured "
        f"(status={refreshed.status!r}, error={refreshed.last_error!r})"
    )

    # The router must have been invoked (i.e., we did not short-circuit).
    mock_deliver.assert_called_once()

    # An oversize-routed-stk event should have been recorded.
    msgs = [e.kind for e in deps.events.recent_for_book(book.id, limit=50)]
    assert "oversize-routed-stk" in msgs, (
        f"expected an oversize-routed-stk event, got kinds: {msgs}"
    )


def test_oversize_with_stk_unconfigured_still_bounces_to_needs_review(tmp_path):
    """Regression guard: when STK is NOT configured and the file is too
    large for SMTP, the book MUST still bounce to needs_review.
    """
    deps = _make_deps(tmp_path)
    book = _seed_book(deps)
    big_file = _make_oversize_epub(tmp_path)

    with patch(
        "endless_library.kindle_stk.KindleStkService.is_configured",
        return_value=False,
    ):
        result = _process_from_downloaded(deps, book, big_file)

    assert result == "needs_review", f"expected needs_review, got {result!r}"
    refreshed = deps.books.get(book.id)
    assert refreshed.status == "needs_review"
    assert "STK not configured" in (refreshed.last_error or ""), (
        f"error message should explain why this is unrecoverable; got "
        f"{refreshed.last_error!r}"
    )
