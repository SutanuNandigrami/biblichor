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
    assert decide_auto_pick(top=75, second=60, threshold=70, gap=10) == "auto"
    assert decide_auto_pick(top=75, second=70, threshold=70, gap=10) == "needs_review"
    assert decide_auto_pick(top=65, second=10, threshold=70, gap=10) == "needs_review"
    assert decide_auto_pick(top=35, second=10, threshold=70, gap=10) == "failed"
    assert decide_auto_pick(top=70, second=0, threshold=70, gap=10) == "auto"
