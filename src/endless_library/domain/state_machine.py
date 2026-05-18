from __future__ import annotations

from typing import Literal

State = Literal[
    "queued",
    "searching",
    "needs_review",
    "downloading",
    "converting",
    "sending",
    "sent",
    "skipped",
    "failed",
]

STATES: tuple[State, ...] = (
    "queued",
    "searching",
    "needs_review",
    "downloading",
    "converting",
    "sending",
    "sent",
    "skipped",
    "failed",
)

# Source of truth for valid book-status transitions. Verified against
# pipeline.process_one + pipeline._process_from_downloaded + the
# /books/{id}/retry reset-to-queued path. Kept in sync with the actual
# transitions the pipeline performs (not aspirational).
_LEGAL: dict[str, set[str]] = {
    "queued": {"searching"},
    # search can land in any of these depending on candidate quality
    "searching": {"needs_review", "downloading", "failed", "skipped"},
    # retry button resets to queued for a fresh search
    "needs_review": {"downloading", "skipped", "queued"},
    "downloading": {"converting", "sending", "failed"},
    "converting": {"sending", "failed"},
    # `sending → downloading` is the PDF→EPUB rescue path swapping the file
    # `sending → needs_review` is the SMTP size guard rejecting an oversize
    #   candidate at the last stage
    "sending": {"sent", "failed", "needs_review", "downloading"},
    "failed": {"searching", "skipped", "queued"},
    # Both terminal states can be re-queued by the retry button
    "sent": {"queued"},
    "skipped": {"queued"},
}


def is_legal_transition(frm: str, to: str) -> bool:
    return to in _LEGAL.get(frm, set())


AutoPickDecision = Literal["auto", "needs_review", "failed"]


def decide_auto_pick(
    *,
    top: float,
    second: float,
    threshold: float,
    gap: float,
    min_score_for_failure: float = 40.0,
    high_confidence_bonus: float = 10.0,
) -> AutoPickDecision:
    """Decide whether the top-scored candidate is good enough to auto-pick.

    - Below min_score_for_failure: nothing plausibly matches; fail outright.
    - At/above threshold with the required gap over #2: auto-pick.
    - High-confidence (>= threshold + high_confidence_bonus): auto-pick even
      with no gap. Multiple high-scoring candidates are usually duplicate
      uploads of the same book; Anna's relevance order picks the right one.
    - Anything else: surface for manual review.
    """
    if top < min_score_for_failure:
        return "failed"
    if top >= threshold + high_confidence_bonus:
        return "auto"
    if top >= threshold and (top - second) >= gap:
        return "auto"
    return "needs_review"
