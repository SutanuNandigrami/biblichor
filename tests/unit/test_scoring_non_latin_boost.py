"""Tier-2 fix: non-Latin same-script title boost.

When both query and candidate titles contain non-Latin glyphs:
  1. Extract the non-Latin substring from each (handles transliterated
     candidates like 'Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)').
  2. Compute fuzz on substrings, take max(full_match, substring_match).
  3. Multiply title_similarity by cfg.non_latin_title_multiplier.

Latin-only flows must be byte-for-byte unaffected.
"""

from __future__ import annotations

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.domain.scoring import _non_latin_substring, score_candidate


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
        audio_keywords=["audiobook", "audible", "mp3", "m4b"],
        non_latin_title_multiplier=2.0,
    )
    base.update(overrides)
    return ScoringCfg(**base)


def _q(**kw) -> SearchQuery:
    base = dict(
        title="কাঁটায়-কাঁটায় ৬",
        author="Narayan Sanyal",
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
        title="Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)",
        author=None,
        language="bn",
        format="pdf",
        filesize_bytes=21_000_000,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="u",
    )
    base.update(kw)
    return Candidate(**base)


# ---------- _non_latin_substring ----------


def test_substring_strips_latin_transliteration():
    out = _non_latin_substring("Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)")
    # The Latin "Kantai Kantai" and the "()" should be gone; the digit "6"
    # and the Bengali glyphs remain.
    assert "Kantai" not in out
    assert "কাঁটায়" in out
    assert "৬" in out
    # "6" is a shared ASCII digit — kept on purpose so a query like "৬"
    # vs candidate "6" still gets some similarity.
    assert "6" in out


def test_substring_keeps_dashes_and_spaces():
    out = _non_latin_substring("কাঁটায়-কাঁটায় ৬")
    assert "-" in out
    assert " " in out


def test_substring_empty_for_pure_ascii():
    assert _non_latin_substring("The Hate U Give").strip() == ""


# ---------- title boost end-to-end ----------


def test_non_latin_match_boosted_vs_no_boost():
    """Same query + candidate, the multiplier swaps in: score must go up."""
    q = _q()
    c = _c()

    s_boost = score_candidate(c, q, _cfg(non_latin_title_multiplier=2.0))
    s_no = score_candidate(c, q, _cfg(non_latin_title_multiplier=1.0))

    assert s_boost.total > s_no.total
    # Boosted title component is at least ~1.9x of unboosted (small slack
    # for total-clamp behavior)
    boosted_t = s_boost.components["title_similarity"]
    unboosted_t = s_no.components["title_similarity"]
    assert boosted_t > unboosted_t * 1.5


def test_transliterated_candidate_beats_unrelated_bengali():
    """Bengali query vs (a) transliterated correct candidate and (b) a
    same-script-but-unrelated Bengali title — the correct one should win."""
    q = _q()
    correct = _c(title="Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)", format="pdf")
    unrelated = _c(
        title="রামায়ণ",  # Bengali Ramayana — same script, different content
        format="epub",
        filesize_bytes=400_000,
    )
    s_correct = score_candidate(correct, q, _cfg())
    s_unrelated = score_candidate(unrelated, q, _cfg())
    assert s_correct.total > s_unrelated.total + 10


def test_no_boost_for_latin_only_match():
    """English-on-English: multiplier MUST NOT kick in."""
    q = SearchQuery(
        title="The Hate U Give",
        author="Angie Thomas",
        isbn13="9780062498533",
        format_priority=("epub",),
        language="en",
    )
    c = Candidate(
        provider="annas",
        md5="a" * 32,
        title="The Hate U Give",
        author="Angie Thomas",
        language="en",
        format="epub",
        filesize_bytes=1_500_000,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="u",
    )
    # With multiplier=2 the score MUST equal the score with multiplier=1
    s_two = score_candidate(c, q, _cfg(non_latin_title_multiplier=2.0))
    s_one = score_candidate(c, q, _cfg(non_latin_title_multiplier=1.0))
    assert s_two.total == s_one.total


def test_pure_bengali_match_now_clears_min_failure_floor():
    """The whole point: with the tier-2 boost, a near-perfect Bengali
    title match against a Bengali candidate must produce a score well
    above the 40-pt failure floor so the book lands in needs_review
    rather than failing outright."""
    q = _q(title="কাঁটায়-কাঁটায় ৬")
    c = _c(
        title="কাঁটায়-কাঁটায় ৬",  # identical
        format="epub",
        filesize_bytes=500_000,
        language="bn",
    )
    s = score_candidate(c, q, _cfg())
    assert s.total >= 50, f"expected at least 50 with tier-2 boost, got {s.total}"


def test_boost_takes_substring_max_with_full_match():
    """When the substring match is HIGHER than the full match, we should
    take the substring (max of the two)."""
    q = _q(title="কাঁটায়-কাঁটায় ৬")
    # Candidate has lots of Latin padding around the matching Bengali;
    # full-string token_set_ratio would be polluted, substring gives clean
    # match.
    c = _c(title="Bengali Title Translation - Kantai Kantai 6 (কাঁটায়-কাঁটায় ৬)")
    s = score_candidate(c, q, _cfg())
    # Title similarity should be at or near its max contribution
    t = s.components["title_similarity"]
    # title_weight (25) x multiplier (2) x t_sim ≥ ~40
    assert t >= 40, f"expected title boost ≥ 40, got {t}"
