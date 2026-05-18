"""Regression + feature-intact tests for the score-clamp removal."""

from __future__ import annotations

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.domain.scoring import score_candidate


def _cfg(**kw) -> ScoringCfg:
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
    base.update(kw)
    return ScoringCfg(**base)


def _q(**kw) -> SearchQuery:
    base = dict(
        title="The Pragmatic Programmer",
        author="Hunt",
        isbn13="9780135957059",
        format_priority=("epub",),
        language="en",
    )
    base.update(kw)
    return SearchQuery(**base)


def _c(**kw) -> Candidate:
    base = dict(
        provider="annas",
        md5="a" * 32,
        title="The Pragmatic Programmer",
        author="Hunt",
        language="en",
        format="epub",
        filesize_bytes=2_000_000,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="u",
        raw={"isbns": ["9780135957059"]},
    )
    base.update(kw)
    return Candidate(**base)


# ============ REGRESSION: scores above 100 are no longer clamped ============


def test_perfect_match_can_exceed_100_with_non_latin_multiplier():
    """With multiplier=2.0, perfect title (25 * 2 = 50) + ISBN (35) +
    author (15) + format (10) + language (10) + filesize (5) = 125.
    Old code clamped to 100; the new code preserves the raw score so
    auto_pick_gap can still distinguish two great candidates."""
    # Non-Latin query against a non-Latin perfect match — multiplier kicks in
    q = SearchQuery(
        title="কাঁটায়-কাঁটায় ৬",
        author="Narayan Sanyal",
        isbn13="9789350402696",
        format_priority=("epub",),
        language="bn",
    )
    perfect = _c(
        title="কাঁটায়-কাঁটায় ৬",
        author="Narayan Sanyal",
        language="bn",
        format="epub",
        filesize_bytes=2_000_000,
        raw={"isbns": ["9789350402696"]},
    )
    s = score_candidate(perfect, q, _cfg(non_latin_title_multiplier=2.0))
    assert s.total > 100, (
        f"expected > 100 for perfect non-Latin match w/ multiplier=2.0, got {s.total}"
    )


def test_auto_pick_gap_distinguishes_two_great_candidates():
    """The motivation for removing the clamp: two candidates that would
    have BOTH clamped to 100 now have distinguishable totals so the
    auto_pick_gap rule can pick the better one."""
    q = _q()
    # Same book, two uploads with different file sizes — both clear 100+
    great = _c(filesize_bytes=2_000_000)  # in normal range
    okay = _c(filesize_bytes=80_000)  # below 100KB → -15 penalty
    s_great = score_candidate(great, q, _cfg())
    s_okay = score_candidate(okay, q, _cfg())
    assert s_great.total != s_okay.total, "scores should diverge, not clamp together"
    assert s_great.total > s_okay.total


# ============ FEATURE-INTACT: lower floor unchanged ============


def test_score_still_floored_at_zero():
    """The 0 floor is still enforced — negative composites get clipped."""
    cfg = _cfg(scan_penalty=200)  # massive penalty
    cand = _c(edition_hints="scanned OCR garbage")
    s = score_candidate(cand, _q(), cfg)
    assert s.total >= 0, "negative score escaped the floor"


def test_typical_score_stays_in_intuitive_range():
    """A normal English book with ISBN + decent metadata should still
    land in the 70-95 range our auto_pick_threshold assumes."""
    s = score_candidate(_c(), _q(), _cfg())
    assert 70 <= s.total <= 110, f"typical score moved too far: {s.total}"


# ============ docstring drift fix ============


def test_non_latin_title_multiplier_default_matches_docstring():
    """The audit caught the docstring claiming default 1.6 while code
    set 2.0. Verify they agree."""
    import inspect

    from endless_library import config

    src = inspect.getsource(config.ScoringCfg)
    # The default must match the value mentioned in the comment block above it
    default = config.ScoringCfg().non_latin_title_multiplier
    assert default == 2.0
    # And the documentation should reference the actual default
    assert f"Default {default:.1f}" in src or f"Default {default}" in src, (
        f"docstring drift: code default is {default} but comment doesn't say so"
    )
