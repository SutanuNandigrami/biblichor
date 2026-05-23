"""Phase 6w.6 — BDeBooks + content-filter abstraction tests."""
from __future__ import annotations


# ============ Task 1: Candidate.categories field ============


def test_candidate_categories_default_empty():
    from endless_library.domain.models import Candidate

    c = Candidate(
        provider="annas",
        md5=None,
        title="Test",
        author=None,
        language="en",
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="https://example.com",
    )
    assert c.categories == ()


def test_candidate_categories_can_be_set():
    from endless_library.domain.models import Candidate

    c = Candidate(
        provider="bdebooks",
        md5=None,
        title="ইসলামিক বই",
        author=None,
        language="bn",
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="https://bdebooks.com/b/test",
        categories=("ইসলামিক বই", "ধর্ম"),
    )
    assert c.categories == ("ইসলামিক বই", "ধর্ম")
