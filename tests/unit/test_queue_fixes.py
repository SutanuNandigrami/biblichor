"""Tests for the two queue-tuning fixes:
1. auto_pick_threshold_non_latin replaces auto_pick_threshold for
   non-Latin queries
2. deliverable_max_bytes hard-skip in scoring lets the picker
   naturally choose smaller candidates when bigger ones would
   trip the SMTP cap downstream
"""

from __future__ import annotations

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.domain.scoring import score_candidate
from endless_library.domain.state_machine import decide_auto_pick


def _cfg(**overrides) -> ScoringCfg:
    base = dict(
        isbn_match=35,
        title_weight=25,
        author_weight=15,
        format_bonus={"epub": 10, "azw3": 9, "mobi": 8, "pdf": 5},
        language_bonus=10,
        filesize_min_bytes=200_000,
        filesize_max_bytes=80 * 1024 * 1024,
        scan_penalty=10,
        audio_keywords=["audiobook"],
        non_latin_title_multiplier=2.0,
        deliverable_max_bytes=None,
    )
    base.update(overrides)
    return ScoringCfg(**base)


def _q(**kw) -> SearchQuery:
    base = dict(
        title="কালপুরুষ",
        author="Samaresh Majumdar",
        isbn13=None,
        format_priority=("epub", "azw3", "mobi", "pdf"),
        language="bn",
    )
    base.update(kw)
    return SearchQuery(**base)


def _c(**kw) -> Candidate:
    base = dict(
        provider="annas",
        md5="a" * 32,
        title="কালপুরুষ",
        author=None,
        language="bn",
        format="pdf",
        filesize_bytes=10 * 1024 * 1024,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="u",
    )
    base.update(kw)
    return Candidate(**base)


# ============ deliverable_max_bytes hard-skip ============


def test_deliverable_cap_oversize_hard_skips():
    """When deliverable_max_bytes is set and candidate is bigger, it must
    hard-skip with a clear oversize reason."""
    cap = 22 * 1024 * 1024 // 1.4  # 22 MB Gmail cap inflated
    cand = _c(filesize_bytes=60 * 1024 * 1024)  # 60 MB PDF
    s = score_candidate(cand, _q(), _cfg(deliverable_max_bytes=int(cap)))
    assert s.is_hard_skip
    assert s.skip_reason is not None
    assert "oversize" in s.skip_reason
    assert s.total == 0.0


def test_deliverable_cap_under_passes():
    cap = 22 * 1024 * 1024 // 1.4
    cand = _c(filesize_bytes=5 * 1024 * 1024)  # well under
    s = score_candidate(cand, _q(), _cfg(deliverable_max_bytes=int(cap)))
    assert not s.is_hard_skip
    assert s.total > 0


def test_deliverable_cap_none_is_no_op():
    """Backward-compat: if deliverable_max_bytes is None, no oversize
    filter is applied."""
    cand = _c(filesize_bytes=500 * 1024 * 1024)  # 500 MB
    s = score_candidate(cand, _q(), _cfg(deliverable_max_bytes=None))
    assert not s.is_hard_skip


def test_deliverable_cap_size_unknown_skipped_check():
    """If candidate has no filesize_bytes (Anna's sometimes omits it),
    we can't enforce the cap — let it through."""
    cap = 22 * 1024 * 1024 // 1.4
    cand = _c(filesize_bytes=None)
    s = score_candidate(cand, _q(), _cfg(deliverable_max_bytes=int(cap)))
    assert not s.is_hard_skip


def test_picker_prefers_smaller_when_bigger_oversize():
    """End-to-end: with a cap, a 65MB PDF gets skipped while a 5MB EPUB
    of the same book wins. This is the real user fix for stuck PDFs."""
    cap = int(22 * 1024 * 1024 / 1.4)
    cfg = _cfg(deliverable_max_bytes=cap)
    big = _c(title="কালপুরুষ", format="pdf", filesize_bytes=65 * 1024 * 1024)
    small = _c(title="কালপুরুষ", format="epub", filesize_bytes=5 * 1024 * 1024)
    s_big = score_candidate(big, _q(), cfg)
    s_small = score_candidate(small, _q(), cfg)
    assert s_big.is_hard_skip and not s_small.is_hard_skip
    assert s_small.total > 0


# ============ auto_pick_threshold_non_latin (via decide_auto_pick) ============


def test_decide_auto_pick_with_low_non_latin_threshold():
    """At Latin's 70-point threshold a Bengali score-65 book stays in
    needs_review. At the non-Latin 45-point threshold the same score
    auto-picks (assuming gap rule satisfied)."""
    # Same input, two thresholds:
    high = decide_auto_pick(top=65, second=50, threshold=70, gap=10, min_score_for_failure=25)
    low = decide_auto_pick(top=65, second=50, threshold=45, gap=10, min_score_for_failure=25)
    assert high == "needs_review"
    assert low == "auto"


def test_decide_auto_pick_non_latin_below_floor_fails():
    """The lower failure floor (25) means score=20 still fails."""
    r = decide_auto_pick(top=20, second=15, threshold=45, gap=10, min_score_for_failure=25)
    assert r == "failed"


def test_decide_auto_pick_non_latin_borderline_review():
    """Top score between floor (25) and threshold (45) → needs_review."""
    r = decide_auto_pick(top=35, second=30, threshold=45, gap=10, min_score_for_failure=25)
    assert r == "needs_review"


def test_decide_auto_pick_non_latin_high_confidence_no_gap_required():
    """If top >= threshold + high_confidence_bonus (default 10), the
    gap rule is waived so duplicate uploads don't block auto-pick."""
    r = decide_auto_pick(
        top=58,
        second=57,
        threshold=45,
        gap=10,
        min_score_for_failure=25,
        high_confidence_bonus=10,
    )
    # 58 >= 45 + 10 → auto, even without gap
    assert r == "auto"
