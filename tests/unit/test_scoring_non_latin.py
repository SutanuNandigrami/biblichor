"""Regression tests for the Bengali-query bug.

Concrete scenario: user adds book #48 — title `কাঁটায়-কাঁটায় ৬` by
"Narayan Sanyal", no ISBN. Anna's returns the correct Bengali book at the
top but also pads results with English fallbacks (Ramayana, Mahabharata,
"The Very Best of R.K. Narayan", etc) because we hint `lang=en`.

The bug was twofold:

  1. The author fallback (when candidate row has no parsed author) credited
     a single shared surname token like "narayan" against any candidate
     mentioning it. Ramayana scored 41.7 — tied with the correct book.

  2. The query was Bengali but the scorer happily ranked English-only
     candidates. There was no script-mismatch guard.

These tests pin down the fix:
  - Multi-token author fallback requires every q_author token in haystack.
  - Cross-script candidates hard-skip.
  - Same-script candidates with a real Bengali title still score normally.
"""

from __future__ import annotations

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.domain.scoring import (
    _author_match_strict,
    _is_non_latin,
    score_candidate,
)


def _cfg() -> ScoringCfg:
    return ScoringCfg(
        isbn_match=35,
        title_weight=25,
        author_weight=15,
        format_bonus={"epub": 10, "azw3": 9, "mobi": 8, "pdf": 5},
        language_bonus=10,
        filesize_min_bytes=200_000,
        filesize_max_bytes=80 * 1024 * 1024,
        scan_penalty=10,
        audio_keywords=["audiobook", "audible", "mp3", "m4b"],
    )


def _bengali_query() -> SearchQuery:
    return SearchQuery(
        title="কাঁটায়-কাঁটায় ৬",
        author="Narayan Sanyal",
        isbn13=None,
        format_priority=("epub", "azw3", "mobi", "pdf"),
        language="en",
    )


def _cand(**kw) -> Candidate:
    base = dict(
        provider="annas",
        md5="a" * 32,
        title="The Ramayana",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=300_000,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="u",
    )
    base.update(kw)
    return Candidate(**base)


# ---------- _is_non_latin ----------


def test_is_non_latin_detects_bengali():
    assert _is_non_latin("কাঁটায়-কাঁটায় ৬")


def test_is_non_latin_detects_devanagari():
    assert _is_non_latin("रामायण")


def test_is_non_latin_detects_cjk():
    assert _is_non_latin("我的奮鬥")


def test_is_non_latin_returns_false_for_pure_ascii():
    assert not _is_non_latin("The Hate U Give")


def test_is_non_latin_accepts_diacritics_as_latin():
    """Accented Latin (Café, Süß) is still Latin — don't flag it."""
    assert not _is_non_latin("Café Brasileiro Süß")


def test_is_non_latin_handles_mixed_script():
    """Transliterated titles ('Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)') have
    Bengali glyphs even though they start with Latin — that still counts."""
    assert _is_non_latin("Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)")


# ---------- _author_match_strict ----------


def test_author_strict_requires_every_token():
    # Single-token overlap on common surname → no credit (the bug)
    assert _author_match_strict("narayan sanyal", "the ramayana by r.k. narayan") == 0.0


def test_author_strict_credits_full_match():
    assert _author_match_strict(
        "narayan sanyal",
        "narayan sanyal — collected works",
    ) == 1.0


def test_author_strict_ignores_short_initials():
    # "k" is dropped (length <= 2); only "rowling" needs to match
    assert _author_match_strict("j k rowling", "harry potter — j.k. rowling") == 1.0


def test_author_strict_empty_haystack():
    assert _author_match_strict("narayan sanyal", "") == 0.0


def test_author_strict_empty_query():
    assert _author_match_strict("", "the ramayana") == 0.0


# ---------- script-mismatch hard-skip end-to-end ----------


def test_ramayana_hard_skipped_for_bengali_query():
    """The exact failure mode from book #48 — Ramayana must drop out."""
    q = _bengali_query()
    cand = _cand(
        title="The Ramayana",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=307_000,
        raw={"row_text": "the ramayana by r.k. narayan"},
    )
    s = score_candidate(cand, q, _cfg())
    assert s.is_hard_skip, "Pure-Latin candidate for Bengali query must hard-skip"
    assert s.skip_reason == "script_mismatch"
    assert s.total == 0.0


def test_correct_bengali_candidate_still_scores():
    """Same query, but a candidate whose title actually has Bengali glyphs
    must NOT hard-skip and must accumulate title similarity."""
    q = _bengali_query()
    cand = _cand(
        title="Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)",
        author="Narayan Sanyal",
        language="bn",
        format="pdf",
        filesize_bytes=21_000_000,
    )
    s = score_candidate(cand, q, _cfg())
    assert not s.is_hard_skip
    assert s.total > 0


def test_devanagari_query_skips_pure_ascii_candidate():
    q = SearchQuery(
        title="रामायण",
        author=None,
        isbn13=None,
        format_priority=("epub",),
        language="hi",
    )
    cand = _cand(title="Practical Hypnotism", format="epub", language="en")
    s = score_candidate(cand, q, _cfg())
    assert s.is_hard_skip and s.skip_reason == "script_mismatch"


def test_latin_query_keeps_latin_candidate():
    """The new guard MUST NOT affect English-only flows."""
    q = SearchQuery(
        title="The Hate U Give",
        author="Angie Thomas",
        isbn13="9780062498533",
        format_priority=("epub",),
        language="en",
    )
    cand = _cand(
        title="The Hate U Give",
        author="Angie Thomas",
        language="en",
        format="epub",
        filesize_bytes=1_500_000,
    )
    s = score_candidate(cand, q, _cfg(), isbn13_match=True)
    assert not s.is_hard_skip
    # ISBN (35) + title (25) + author (15) + format (10) + language (10)
    # + filesize (5) = 100, clamped
    assert s.total >= 70


# ---------- author fallback no longer credits "narayan" alone ----------


def test_score_drops_for_partial_surname_match():
    """Even without the script guard (e.g. if Anna's ever returns a Bengali
    Ramayana edition), the single-token "narayan" coincidence must no
    longer inflate the score."""
    q = SearchQuery(
        title="কাঁটায়-কাঁটায় ৬",
        author="Narayan Sanyal",
        isbn13=None,
        format_priority=("epub",),
        language="bn",
    )
    cand = _cand(
        title="রামায়ণ",  # Bengali Ramayana — same-script, won't hard-skip
        author=None,
        language="bn",
        format="epub",
        filesize_bytes=400_000,
        raw={"row_text": "ramayana by r.k. narayan (bengali edition)"},
    )
    s = score_candidate(cand, q, _cfg())
    assert not s.is_hard_skip
    # author_similarity must be 0 (sanyal missing) — score is just
    # format(10) + language(10) + filesize(5) = 25, well below the 40 floor
    assert s.components.get("author_similarity", 0.0) == 0.0
    # Below the 40 min_score_for_failure floor — the pipeline drops it.
    # With the tier-2 boost we accept up to ~50 here; the important contract
    # is that the wrong same-script candidate stays well below the 70 auto-pick
    # threshold (so the correct candidate wins the gap check).
    assert s.total < 50
