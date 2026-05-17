from __future__ import annotations

import pytest

from endless_library.domain.state_machine import STATES, decide_auto_pick, is_legal_transition


@pytest.mark.parametrize(
    "frm,to,legal",
    [
        ("queued", "searching", True),
        ("searching", "needs_review", True),
        ("searching", "downloading", True),
        ("searching", "failed", True),
        ("downloading", "converting", True),
        ("downloading", "sending", True),
        ("converting", "sending", True),
        ("sending", "sent", True),
        ("failed", "searching", True),
        ("sent", "queued", False),
        ("skipped", "downloading", False),
        ("queued", "sent", False),
        ("downloading", "queued", False),
    ],
)
def test_transition_legality(frm: str, to: str, legal: bool) -> None:
    assert is_legal_transition(frm, to) is legal


def test_states_known() -> None:
    assert STATES == (
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


def test_auto_pick_thresholds() -> None:
    # Normal flow: above threshold, gap satisfied
    assert decide_auto_pick(top=75, second=60, threshold=70, gap=10) == "auto"
    # Below min_score_for_failure
    assert decide_auto_pick(top=35, second=10, threshold=70, gap=10) == "failed"
    # Below threshold but above failure floor
    assert decide_auto_pick(top=65, second=10, threshold=70, gap=10) == "needs_review"
    # At threshold + huge gap
    assert decide_auto_pick(top=70, second=0, threshold=70, gap=10) == "auto"


def test_auto_pick_high_confidence_overrides_gap() -> None:
    # top is well above threshold (threshold + 10) -> auto even with no gap
    assert decide_auto_pick(top=80, second=80, threshold=70, gap=10) == "auto"
    # Just barely above threshold, no gap -> needs_review
    assert decide_auto_pick(top=70, second=70, threshold=70, gap=5) == "needs_review"
    # Tunable bonus: with bonus=10, need top>=80 to override gap
    assert (
        decide_auto_pick(top=85, second=85, threshold=70, gap=5, high_confidence_bonus=10) == "auto"
    )
    # With bonus=20, top=85 < 90 so gap rule applies
    assert (
        decide_auto_pick(top=85, second=85, threshold=70, gap=5, high_confidence_bonus=20)
        == "needs_review"
    )
