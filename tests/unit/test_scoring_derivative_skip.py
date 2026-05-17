"""Derivative-content keyword filter (summary, study guide, etc.)."""

from __future__ import annotations

from endless_library.config import ScoringCfg
from endless_library.domain.models import Candidate, SearchQuery
from endless_library.domain.scoring import score_candidate


def _cfg():
    return ScoringCfg(audio_keywords=["audiobook"])


def _q():
    return SearchQuery(
        title="Sapiens", author="Harari", isbn13=None, format_priority=("epub",), language="en"
    )


def _c(title=None, hints=""):
    return Candidate(
        provider="annas",
        md5="a" * 32,
        title=title or "Sapiens",
        author="Harari",
        language="en",
        format="epub",
        filesize_bytes=2_000_000,
        year=2014,
        publisher=None,
        edition_hints=hints,
        detail_url="u",
    )


def test_summary_hard_skip():
    s = score_candidate(_c(title="Summary of Sapiens"), _q(), _cfg())
    assert s.is_hard_skip
    assert "summary" in (s.skip_reason or "")


def test_conversation_starters_hard_skip():
    s = score_candidate(_c(title="Sapiens: Conversation Starters"), _q(), _cfg())
    assert s.is_hard_skip


def test_study_guide_hard_skip():
    s = score_candidate(_c(title="A Study Guide for Sapiens"), _q(), _cfg())
    assert s.is_hard_skip


def test_real_book_not_skipped():
    s = score_candidate(_c(title="Sapiens: A Brief History of Humankind"), _q(), _cfg())
    assert not s.is_hard_skip
