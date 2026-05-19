"""Regression + feature-intact tests for the ISBN+title auto-pick override.

Audit goal: reduce manual review. Books where the top candidate has a
solid ISBN13 match AND title similarity >= 0.92 are identity-confirmed
even if total score is low (e.g. odd filesize, missing format hint).
These should never sit in needs_review.

Regression: identity-strong candidate with sub-threshold total gets
auto-picked. Feature-intact: weak identity, near-threshold totals,
and the existing high-confidence rules all still work.
"""

from __future__ import annotations

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.domain.scoring import score_candidate
from endless_library.domain.state_machine import decide_auto_pick

# ============ REGRESSION: ISBN + title override fires ============


def test_override_auto_picks_when_isbn_and_title_match_even_with_low_total():
    """The audit-target case: top is below threshold (e.g. 55 vs 70)
    but identity signals are rock-solid (ISBN matched, title sim 0.95).
    Previously -> needs_review. Now -> auto."""
    d = decide_auto_pick(
        top=55.0,  # below threshold 70
        second=10.0,
        threshold=70.0,
        gap=10.0,
        min_score_for_failure=40.0,
        top_isbn_matched=True,
        top_title_similarity=0.95,
    )
    assert d == "auto"


def test_override_does_not_fire_without_isbn_match():
    """Title similarity alone (even 1.0) is not enough — ISBN is the
    identity anchor. Without it, fall back to threshold + gap rules."""
    d = decide_auto_pick(
        top=55.0,
        second=10.0,
        threshold=70.0,
        gap=10.0,
        top_isbn_matched=False,  # no ISBN
        top_title_similarity=1.00,  # perfect title
    )
    assert d == "needs_review"


def test_override_does_not_fire_below_similarity_threshold():
    """ISBN matched but title is only 0.85 similar (below the 0.92
    default). Could be wrong-edition / wrong-volume — let human review."""
    d = decide_auto_pick(
        top=55.0,
        second=10.0,
        threshold=70.0,
        gap=10.0,
        top_isbn_matched=True,
        top_title_similarity=0.85,  # below 0.92
    )
    assert d == "needs_review"


def test_override_threshold_is_configurable():
    """Power users can tighten the override (e.g. 0.98)."""
    d_tight = decide_auto_pick(
        top=55.0,
        second=10.0,
        threshold=70.0,
        gap=10.0,
        top_isbn_matched=True,
        top_title_similarity=0.93,
        isbn_title_override_min_similarity=0.98,
    )
    assert d_tight == "needs_review"
    d_loose = decide_auto_pick(
        top=55.0,
        second=10.0,
        threshold=70.0,
        gap=10.0,
        top_isbn_matched=True,
        top_title_similarity=0.93,
        isbn_title_override_min_similarity=0.90,
    )
    assert d_loose == "auto"


# ============ REGRESSION: override still respects the failure floor ============


def test_override_does_not_rescue_below_min_score_for_failure():
    """Even with ISBN+title match, a total score below the failure floor
    means the candidate is so broken (huge filesize, audio-flagged, etc.)
    that we should NOT try to download it. Fail outright."""
    d = decide_auto_pick(
        top=20.0,  # below floor 40
        second=0.0,
        threshold=70.0,
        gap=10.0,
        min_score_for_failure=40.0,
        top_isbn_matched=True,
        top_title_similarity=1.00,
    )
    assert d == "failed"


# ============ FEATURE-INTACT: existing decision rules unchanged ============


def test_existing_threshold_gap_rule_still_works():
    """The original auto-pick condition stays intact: at threshold with
    a clear gap to #2."""
    d = decide_auto_pick(top=75, second=60, threshold=70, gap=10)
    assert d == "auto"


def test_existing_high_confidence_rule_still_works():
    """The original high-confidence rule (>= threshold + bonus) still
    auto-picks without a gap."""
    d = decide_auto_pick(top=80, second=80, threshold=70, gap=10, high_confidence_bonus=10)
    assert d == "auto"


def test_existing_needs_review_zone_unchanged_when_no_overrides():
    """When override params are at defaults (False, 0.0), the decision
    matches the pre-Phase-3b behavior."""
    d = decide_auto_pick(top=65, second=10, threshold=70, gap=10)
    assert d == "needs_review"


def test_existing_failure_zone_unchanged():
    d = decide_auto_pick(top=35, second=10, threshold=70, gap=10)
    assert d == "failed"


# ============ FEATURE-INTACT: ScoreBreakdown now exposes the signals ============


def _cfg() -> ScoringCfg:
    return ScoringCfg(
        isbn_match=35,
        title_weight=25,
        author_weight=15,
        format_bonus={"epub": 10},
        language_bonus=10,
        filesize_min_bytes=200_000,
        filesize_max_bytes=80 * 1024 * 1024,
        scan_penalty=10,
        audio_keywords=["audiobook"],
    )


def _q() -> SearchQuery:
    return SearchQuery(
        title="The Pragmatic Programmer",
        author="Hunt",
        isbn13="9780135957059",
        format_priority=("epub",),
        language="en",
    )


def _c(title: str, has_isbn: bool) -> Candidate:
    return Candidate(
        provider="annas",
        md5="a" * 32,
        title=title,
        author="Hunt",
        language="en",
        format="epub",
        filesize_bytes=2_000_000,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="u",
        raw={"isbns": ["9780135957059"] if has_isbn else []},
    )


def test_score_breakdown_exposes_isbn_matched_flag():
    """The override needs to read isbn13_matched from breakdown.components."""
    sb = score_candidate(_c("The Pragmatic Programmer", has_isbn=True), _q(), _cfg())
    assert sb.components.get("isbn13_matched") == 1.0
    sb_no = score_candidate(_c("The Pragmatic Programmer", has_isbn=False), _q(), _cfg())
    assert sb_no.components.get("isbn13_matched") == 0.0


def test_score_breakdown_exposes_title_similarity_raw():
    """Unweighted similarity (0..1) is what the override compares against."""
    sb = score_candidate(_c("The Pragmatic Programmer", has_isbn=True), _q(), _cfg())
    raw = sb.components.get("title_similarity_raw")
    assert raw is not None
    assert 0.0 <= raw <= 1.0
    # Perfect title match should be near 1.0
    assert raw >= 0.95


def test_score_breakdown_title_similarity_raw_low_for_mismatch():
    sb = score_candidate(_c("Completely Different Book", has_isbn=False), _q(), _cfg())
    raw = sb.components.get("title_similarity_raw", 1.0)
    assert raw < 0.5
