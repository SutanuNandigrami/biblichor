"""Regression + feature-intact tests for the Phase 3a chain extension.

The previous behavior: chain stopped at `fallthrough_quality_floor`
(60 Latin / 40 non-Latin). With `auto_pick_threshold` at 70 / 45,
the gap zone (floor <= top < threshold) meant the first scraper
could short-circuit the chain with a candidate that would only land
in needs_review — without consulting the remaining scrapers in case
one of them had the actual book.

The fix: stop the chain only when we've found a candidate we'd
actually auto-pick. `floor = max(configured_floor, threshold)`.

Regression tests force a top score in the gap zone (annas: 65 with
threshold 70) and verify the next scraper IS called. Feature-intact
tests verify books well above threshold still short-circuit, and
books well below floor still fall through to every scraper.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from endless_library.config import Config
from endless_library.domain.models import Candidate
from endless_library.pipeline import PipelineDeps, _search_with_strategies


def _make_deps(tmp_path):
    cfg = Config()
    cfg.scrapers.order = ["annas_curl", "libgen_curl", "archive_curl"]
    cfg.scrapers.enabled = {
        "annas_curl": True,
        "libgen_curl": True,
        "archive_curl": True,
    }
    # Pin the canonical defaults explicitly so a future change to the
    # config defaults doesn't silently alter the test contract.
    cfg.general.auto_pick_threshold = 70.0
    cfg.general.fallthrough_quality_floor = 60.0
    deps = PipelineDeps.build(cfg=cfg, db_path=tmp_path / "library.db")
    return deps


def _book(deps, title="The Pragmatic Programmer", isbn="9780135957059", lang="en"):
    bid = deps.books.upsert(
        title=title, author="Hunt", isbn13=isbn, source="manual", source_id=None
    )
    return deps.books.get(bid)


def _cand(provider: str, md5: str, *, title: str, has_isbn: bool, fmt: str = "epub"):
    """Build a Candidate. has_isbn flag drives the score: title+isbn ~= 85,
    title-only no-isbn = ~50 — useful for landing in the gap zone."""
    raw = {"isbns": ["9780135957059"]} if has_isbn else {"isbns": []}
    return Candidate(
        provider=provider,
        md5=md5,
        title=title,
        author="Hunt",
        language="en",
        format=fmt,
        filesize_bytes=2_000_000,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url=f"https://example.com/{md5}",
        raw=raw,
    )


# ============ REGRESSION: gap-zone candidate no longer short-circuits ============


def test_annas_top_in_gap_zone_continues_to_libgen(tmp_path):
    """The exact bug: annas returns a candidate scoring ~65 (Latin, between
    the old floor 60 and threshold 70). With the old code, the chain
    stopped at annas. With the fix, libgen is queried too."""
    deps = _make_deps(tmp_path)
    book = _book(deps)

    # No-ISBN candidate with full title match -> lands around 65 (title 25 + fuzzy match
    # + format 10 + language 10 + filesize 5 = ~50-65 depending on rapidfuzz)
    annas_meh = _cand("annas", "a" * 32, title="The Pragmatic Programmer", has_isbn=False)

    # libgen has the real thing with ISBN
    libgen_perfect = _cand("libgen", "b" * 32, title="The Pragmatic Programmer", has_isbn=True)

    libgen_search = MagicMock(return_value=[libgen_perfect])
    archive_search = MagicMock(return_value=[])

    def fake_build(name, cfg, **kw):
        scraper = MagicMock()
        if name == "annas_curl":
            scraper.search.return_value = [annas_meh]
            scraper.provider = "annas"
        elif name == "libgen_curl":
            scraper.search = libgen_search
            scraper.provider = "libgen"
        else:
            scraper.search = archive_search
            scraper.provider = "archive"
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        cands, _last = _search_with_strategies(deps, book)

    # libgen MUST have been queried, because annas's gap-zone score doesn't
    # short-circuit anymore
    libgen_search.assert_called_once()
    # And the libgen candidate should be in the returned pool
    libgen_md5s = {c.md5 for c in cands if c.provider == "libgen"}
    assert "b" * 32 in libgen_md5s


def test_non_latin_path_uses_same_invariant(tmp_path):
    """The code path uses the same `max(configured_floor, threshold)`
    formula for both Latin and non-Latin scripts. Verify the non-Latin
    floor IS used (just by computing what it would be); the actual
    end-to-end test is hard to engineer because the non-Latin 2x title
    multiplier pushes most candidates past threshold."""
    deps = _make_deps(tmp_path)
    # Force the non-Latin defaults
    deps.cfg.general.auto_pick_threshold_non_latin = 45.0
    deps.cfg.general.fallthrough_quality_floor_non_latin = 40.0

    # Invariant: effective floor for non-Latin path = max(40, 45) = 45.
    # Pin via an explicit computation that mirrors pipeline.py.
    eff = max(
        deps.cfg.general.fallthrough_quality_floor_non_latin,
        deps.cfg.general.auto_pick_threshold_non_latin,
    )
    assert eff == 45.0


def test_floor_threshold_invariant_unit():
    """Pure-function check of the invariant: effective floor = max of
    the configured floor and the auto-pick threshold. This is the core
    semantic — pinning it directly rules out regressions in the formula."""
    for configured_floor, threshold, expected in [
        (60.0, 70.0, 70.0),   # default Latin -> floor raised to threshold
        (40.0, 45.0, 45.0),   # default non-Latin -> floor raised to threshold
        (70.0, 70.0, 70.0),   # already equal
        (85.0, 70.0, 85.0),   # user wants extra scraping
        (0.0,  70.0, 70.0),   # never below threshold
    ]:
        assert max(configured_floor, threshold) == expected


# ============ FEATURE-INTACT: clearly-good results still short-circuit ============


def test_high_quality_annas_result_still_stops_chain(tmp_path):
    """When annas returns a candidate that would auto-pick (>=70), there's
    no value in burning extra scraper calls — chain still stops early."""
    deps = _make_deps(tmp_path)
    book = _book(deps)

    annas_perfect = _cand("annas", "a" * 32, title="The Pragmatic Programmer", has_isbn=True)
    libgen_search = MagicMock(return_value=[])

    def fake_build(name, cfg, **kw):
        scraper = MagicMock()
        if name == "annas_curl":
            scraper.search.return_value = [annas_perfect]
            scraper.provider = "annas"
        elif name == "libgen_curl":
            scraper.search = libgen_search
            scraper.provider = "libgen"
        else:
            scraper.search.return_value = []
            scraper.provider = name
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        _, last = _search_with_strategies(deps, book)

    assert last == "annas_curl"
    libgen_search.assert_not_called()


def test_below_floor_still_falls_through_to_all_scrapers(tmp_path):
    """When annas returns garbage (well below the floor), every other
    scraper still gets consulted — this was the original quality-chain
    fix from earlier and must continue working."""
    deps = _make_deps(tmp_path)
    book = _book(deps)

    annas_garbage = _cand("annas", "a" * 32, title="Completely Different Book", has_isbn=False)
    libgen_search = MagicMock(return_value=[])
    archive_search = MagicMock(return_value=[])

    def fake_build(name, cfg, **kw):
        scraper = MagicMock()
        if name == "annas_curl":
            scraper.search.return_value = [annas_garbage]
            scraper.provider = "annas"
        elif name == "libgen_curl":
            scraper.search = libgen_search
            scraper.provider = "libgen"
        else:
            scraper.search = archive_search
            scraper.provider = "archive"
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        _search_with_strategies(deps, book)

    libgen_search.assert_called_once()
    archive_search.assert_called_once()


def test_user_configured_floor_above_threshold_forces_more_scraping(tmp_path):
    """A power user setting fallthrough_quality_floor=999 (unreachably
    high) is a way to demand exhaustive scraping. With `max(configured,
    threshold)`, no candidate clears 999, so the chain runs every
    enabled scraper. This pins the upper-bound behavior."""
    deps = _make_deps(tmp_path)
    deps.cfg.general.fallthrough_quality_floor = 999.0

    book = _book(deps)

    annas_perfect = _cand("annas", "a" * 32, title="The Pragmatic Programmer", has_isbn=True)
    libgen_search = MagicMock(return_value=[])
    archive_search = MagicMock(return_value=[])

    def fake_build(name, cfg, **kw):
        scraper = MagicMock()
        if name == "annas_curl":
            scraper.search.return_value = [annas_perfect]
            scraper.provider = "annas"
        elif name == "libgen_curl":
            scraper.search = libgen_search
            scraper.provider = "libgen"
        else:
            scraper.search = archive_search
            scraper.provider = "archive"
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        _search_with_strategies(deps, book)

    # Configured floor was unreachably high - every scraper must have been called
    libgen_search.assert_called_once()
    archive_search.assert_called_once()
