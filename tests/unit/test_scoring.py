from __future__ import annotations

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.domain.scoring import score_candidate


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


def _q(**kw):
    base = dict(
        title="The Pragmatic Programmer",
        author="Hunt",
        isbn13="9780135957059",
        format_priority=("epub", "azw3", "mobi", "pdf"),
        language="en",
    )
    base.update(kw)
    return SearchQuery(**base)


def _c(**kw):
    base = dict(
        provider="annas",
        md5="a" * 32,
        title="The Pragmatic Programmer",
        author="Hunt",
        language="en",
        format="epub",
        filesize_bytes=2_000_000,
        year=2019,
        publisher="Pragmatic",
        edition_hints="",
        detail_url="u",
    )
    base.update(kw)
    return Candidate(**base)


def test_isbn_match_dominates():
    s = score_candidate(_c(), _q(), _cfg(), isbn13_match=True)
    assert s.components["isbn_match"] == 35


def test_audio_is_hard_skip():
    s = score_candidate(_c(edition_hints="full audiobook"), _q(), _cfg())
    assert s.is_hard_skip
    assert s.skip_reason == "audio"
    assert s.total == 0.0


def test_filesize_penalty_for_stub():
    s = score_candidate(_c(filesize_bytes=50_000), _q(), _cfg())
    assert s.components["filesize_penalty"] == -15


def test_format_bonus_picks_priority():
    s_epub = score_candidate(_c(format="epub"), _q(), _cfg())
    s_pdf = score_candidate(_c(format="pdf"), _q(), _cfg())
    assert s_epub.components["format_bonus"] == 10
    assert s_pdf.components["format_bonus"] == 5


def test_title_similarity_scaled():
    s_exact = score_candidate(_c(title="The Pragmatic Programmer"), _q(), _cfg())
    s_close = score_candidate(_c(title="Pragmatic Programmer 20th anniversary"), _q(), _cfg())
    s_far = score_candidate(_c(title="Some Other Book Entirely"), _q(), _cfg())
    assert (
        s_exact.components["title_similarity"]
        > s_close.components["title_similarity"]
        > s_far.components["title_similarity"]
    )


def test_language_bonus_only_when_match():
    s_en = score_candidate(_c(language="en"), _q(language="en"), _cfg())
    s_de = score_candidate(_c(language="de"), _q(language="en"), _cfg())
    assert s_en.components["language_bonus"] == 10
    assert s_de.components["language_bonus"] == 0


def test_scan_penalty():
    s = score_candidate(_c(edition_hints="scanned, OCR"), _q(), _cfg())
    assert s.components["scan_penalty"] == -10


def test_total_floored_at_zero():
    # The upper clamp was removed (scores can now exceed 100 with the non-Latin
    # multiplier + ISBN + author + format + language + filesize stack); the
    # lower floor at 0 stays so heavy penalties don't produce negative totals.
    s = score_candidate(_c(filesize_bytes=10), _q(), _cfg())
    assert s.total >= 0.0
