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

_LEGAL: dict[str, set[str]] = {
    "queued": {"searching"},
    "searching": {"needs_review", "downloading", "failed"},
    "needs_review": {"downloading", "skipped"},
    "downloading": {"converting", "sending", "failed"},
    "converting": {"sending", "failed"},
    "sending": {"sent", "failed"},
    "failed": {"searching", "skipped"},
    "sent": set(),
    "skipped": set(),
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
