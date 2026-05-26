"""Tests for ultrareview I9 (doab.py metadata first-wins)
and I10 (oapen_doab.py metadata first-wins).

DOAB/OAPEN REST API returns metadata as a list of {key, value} objects.
A key like dc.identifier.uri can appear multiple times (DOI handle, OAPEN
handle, cover URL, etc.). The old dict comprehension silently kept only the
last value, which could overwrite the primary download URL with a secondary
identifier.

Fix: keep the FIRST occurrence of each key (Dublin Core convention: primary
value is listed first).
"""

from __future__ import annotations


def _make_doab_item(metadata: list[dict]) -> dict:
    return {"metadata": metadata}


# ---------------------------------------------------------------------------
# I9 -- doab.py: first dc.identifier.uri wins
# ---------------------------------------------------------------------------


def test_doab_keeps_first_metadata_value():
    """doab.Doab.search must use the FIRST dc.identifier.uri, not the last."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from endless_library.scrapers.doab import Doab

    cfg = SimpleNamespace()

    # Two dc.identifier.uri: first is the PDF download, second is a DOI page.
    item = _make_doab_item(
        [
            {"key": "dc.title", "value": "Open Access Book"},
            {"key": "dc.identifier.uri", "value": "https://download.example.com/book.pdf"},
            {"key": "dc.identifier.uri", "value": "https://doi.org/10.1234/5678"},
        ]
    )

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = [item]

    class _FakeClient:
        def get(self, url, params=None, **kw):
            return fake_resp

        def close(self):
            pass

    with patch("endless_library.scrapers.doab.make_client", return_value=_FakeClient()):
        scraper = Doab(cfg)
        from endless_library.domain.models import SearchQuery

        sq = SearchQuery("Open Access Book", None, None, ("epub", "pdf"), "en")
        cands = scraper.search(sq)

    assert len(cands) == 1, f"expected 1 candidate, got {len(cands)}"
    assert cands[0].detail_url == "https://download.example.com/book.pdf", (
        f"I9: expected first dc.identifier.uri, got {cands[0].detail_url!r}"
    )


def test_doab_prefers_oapen_relation_over_identifier():
    """oapen.relation.isPartOfBook should win over dc.identifier.uri."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    from endless_library.scrapers.doab import Doab

    cfg = SimpleNamespace()

    item = _make_doab_item(
        [
            {"key": "dc.title", "value": "Scholarly Work"},
            {
                "key": "oapen.relation.isPartOfBook",
                "value": "https://oapen.org/download?type=document&docid=12345",
            },
            {"key": "dc.identifier.uri", "value": "https://doi.org/10.9999/xyz"},
        ]
    )

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = [item]

    class _FakeClient:
        def get(self, url, params=None, **kw):
            return fake_resp

        def close(self):
            pass

    with patch("endless_library.scrapers.doab.make_client", return_value=_FakeClient()):
        scraper = Doab(cfg)
        from endless_library.domain.models import SearchQuery

        sq = SearchQuery("Scholarly Work", None, None, ("epub", "pdf"), "en")
        cands = scraper.search(sq)

    assert len(cands) == 1
    assert "oapen.org" in cands[0].detail_url, (
        f"oapen.relation.isPartOfBook should be preferred, got {cands[0].detail_url!r}"
    )


# ---------------------------------------------------------------------------
# I10 -- oapen_doab.py: first metadata value wins
# ---------------------------------------------------------------------------


def _make_oapen_rec(metadata: list[dict], bitstreams: list[dict] | None = None) -> dict:
    return {
        "metadata": metadata,
        "bitstreams": bitstreams or [{"format": "PDF", "retrieveLink": "/retrieve/123"}],
    }


def test_oapen_doab_keeps_first_title():
    """_build_candidate must use the FIRST dc.title, not the last."""
    from endless_library.scrapers.oapen_doab import _build_candidate

    rec = _make_oapen_rec(
        [
            {"key": "dc.title", "value": "First Title"},
            {"key": "dc.title", "value": "Second Title (should be ignored)"},
        ]
    )

    cand = _build_candidate(rec, "oapen", "https://library.oapen.org")
    assert cand is not None
    assert cand.title == "First Title", f"I10: expected first dc.title, got {cand.title!r}"


def test_oapen_doab_first_wins_for_creator():
    """_build_candidate must use the FIRST dc.creator."""
    from endless_library.scrapers.oapen_doab import _build_candidate

    rec = _make_oapen_rec(
        [
            {"key": "dc.title", "value": "A Book"},
            {"key": "dc.creator", "value": "Primary Author"},
            {"key": "dc.creator", "value": "Secondary Author (should be ignored)"},
        ]
    )

    cand = _build_candidate(rec, "oapen", "https://library.oapen.org")
    assert cand is not None
    assert cand.author == "Primary Author", f"I10: expected first dc.creator, got {cand.author!r}"


def test_oapen_doab_returns_none_without_title():
    """_build_candidate returns None when dc.title is absent."""
    from endless_library.scrapers.oapen_doab import _build_candidate

    rec = _make_oapen_rec(
        [
            {"key": "dc.creator", "value": "Author"},
        ]
    )
    assert _build_candidate(rec, "oapen", "https://library.oapen.org") is None
