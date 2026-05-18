"""Tests for the quality-gated scraper chain in pipeline._search_with_strategies.

The previous "break on first non-empty" behavior was the reason books that
Anna's didn't have just sat at low scores forever — the other scrapers
(libgen, archive, welib) never got a chance. These tests pin down:
  - chain stops EARLY when first scraper clears the floor
  - chain CONTINUES when first scraper's top is below the floor
  - results from multiple scrapers get accumulated + deduped by md5
  - quality floor is script-aware (Latin 60 vs non-Latin 40)
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

from endless_library.config import Config
from endless_library.domain.models import Candidate
from endless_library.pipeline import _search_with_strategies


def _cand(md5: str, score_signal_title: str, fmt: str = "epub", size: int = 2_000_000) -> Candidate:
    return Candidate(
        provider="annas",
        md5=md5,
        title=score_signal_title,
        author=None,
        language="en",
        format=fmt,
        filesize_bytes=size,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url=f"https://annas-archive.gl/md5/{md5}",
        raw={"isbns": []},
    )


def _make_deps(tmp_path):
    from endless_library.pipeline import PipelineDeps

    cfg = Config()
    cfg.scrapers.order = ["annas_curl", "libgen_curl", "archive_curl"]
    cfg.scrapers.enabled = {
        "annas_curl": True,
        "libgen_curl": True,
        "archive_curl": True,
    }
    deps = PipelineDeps.build(cfg=cfg, db_path=tmp_path / "library.db")
    return deps


def _insert_book(deps, **kw):
    base = dict(
        title="The Pragmatic Programmer",
        author="Hunt",
        isbn13="9780135957059",
        source="manual",
        source_id=None,
    )
    base.update(kw)
    bid = deps.books.upsert(**base)
    return deps.books.get(bid)


def test_chain_stops_when_first_scraper_clears_floor(tmp_path):
    deps = _make_deps(tmp_path)
    book = _insert_book(deps)

    # annas_curl returns the actual book with ISBN match -> easily clears 60
    perfect = _cand("a" * 32, "The Pragmatic Programmer")
    perfect = replace(perfect, raw={"isbns": ["9780135957059"]})

    libgen_called = MagicMock()

    def fake_build(name, cfg, **kw):
        scraper = MagicMock()
        if name == "annas_curl":
            scraper.search.return_value = [perfect]
        else:
            libgen_called(name)
            scraper.search.return_value = []
        scraper.provider = "annas" if name == "annas_curl" else name
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        cands, last = _search_with_strategies(deps, book)

    assert len(cands) == 1
    assert last == "annas_curl"
    # libgen / archive must NOT have been built when annas cleared the floor
    libgen_called.assert_not_called()


def test_chain_falls_through_when_top_is_below_floor(tmp_path):
    """Anna's returns garbage (no ISBN match, only fake titles).
    Score must stay below 60, so libgen + archive must both be called."""
    deps = _make_deps(tmp_path)
    book = _insert_book(deps)

    # Garbage candidates — no ISBN, weak title overlap → score will be low
    garbage = [_cand(f"a{i:030d}", "Totally Different Book", "epub") for i in range(3)]
    # libgen has the real book WITH ISBN — should clear floor
    real = _cand("b" * 32, "The Pragmatic Programmer")
    real = replace(real, provider="libgen", raw={"isbns": ["9780135957059"]})

    calls = []

    def fake_build(name, cfg, **kw):
        scraper = MagicMock()
        calls.append(name)
        if name == "annas_curl":
            scraper.search.return_value = garbage
        elif name == "libgen_curl":
            scraper.search.return_value = [real]
        else:
            scraper.search.return_value = []
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        cands, last = _search_with_strategies(deps, book)

    # Both annas and libgen got tried; archive did NOT because libgen cleared
    # the floor at score ~95 (ISBN match alone is 35 + title + format + lang).
    assert "annas_curl" in calls
    assert "libgen_curl" in calls
    assert "archive_curl" not in calls
    # And the libgen result is in the pool
    assert any(c.md5 == "b" * 32 for c in cands)
    assert last == "libgen_curl"


def test_chain_runs_to_exhaustion_when_no_scraper_clears(tmp_path):
    deps = _make_deps(tmp_path)
    book = _insert_book(deps)
    weak = [_cand(f"x{i:030d}", "Unrelated Book") for i in range(2)]

    calls = []

    def fake_build(name, cfg, **kw):
        calls.append(name)
        scraper = MagicMock()
        scraper.search.return_value = weak  # always weak
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        cands, _last = _search_with_strategies(deps, book)

    # All three scrapers attempted
    assert calls == ["annas_curl", "libgen_curl", "archive_curl"]
    # Pool has unique candidates from all three (md5s collide so dedup keeps 2)
    md5s = {c.md5 for c in cands}
    assert len(md5s) == 2


def test_chain_dedupes_by_md5_across_scrapers(tmp_path):
    deps = _make_deps(tmp_path)
    book = _insert_book(deps)
    same_md5 = "f" * 32
    annas_dup = _cand(same_md5, "Junk Title 1")
    libgen_dup = replace(_cand(same_md5, "Junk Title 2"), provider="libgen")
    annas_dup = replace(annas_dup, raw={"isbns": []})

    def fake_build(name, cfg, **kw):
        scraper = MagicMock()
        if name == "annas_curl":
            scraper.search.return_value = [annas_dup]
        elif name == "libgen_curl":
            scraper.search.return_value = [libgen_dup]
        else:
            scraper.search.return_value = []
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        cands, _ = _search_with_strategies(deps, book)

    # Same md5 from two scrapers → one row in the pool
    md5s = [c.md5 for c in cands]
    assert md5s.count(same_md5) == 1


def test_chain_scraper_exception_continues_to_next(tmp_path):
    """If a scraper crashes, log it as an event and continue to the next.
    Old behaviour was already this way for empty results; we keep it."""
    deps = _make_deps(tmp_path)
    book = _insert_book(deps)
    good = _cand("a" * 32, "The Pragmatic Programmer")
    good = replace(good, raw={"isbns": ["9780135957059"]})

    def fake_build(name, cfg, **kw):
        if name == "annas_curl":
            scraper = MagicMock()
            scraper.search.side_effect = RuntimeError("annas down")
            return scraper
        scraper = MagicMock()
        scraper.search.return_value = [good] if name == "libgen_curl" else []
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        cands, last = _search_with_strategies(deps, book)

    # libgen rescued the search
    assert any(c.md5 == "a" * 32 for c in cands)
    assert last == "libgen_curl"


def test_chain_uses_non_latin_floor(tmp_path):
    """For a Bengali book, the floor is 40, not 60. A candidate that scores
    45 (Bengali title match + format + filesize) must STOP the chain."""
    deps = _make_deps(tmp_path)
    bengali = _insert_book(deps, title="কাঁটায়-কাঁটায় ৬", author="Narayan Sanyal", isbn13=None)
    # A same-script candidate that should score ~45 (title 25*2 + format 10 + filesize 5 = 65)
    cand = Candidate(
        provider="annas",
        md5="a" * 32,
        title="কাঁটায়-কাঁটায় ৬",
        author=None,
        language="bn",
        format="epub",
        filesize_bytes=2_000_000,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="https://annas-archive.gl/md5/aaa",
        raw={"isbns": []},
    )

    calls = []

    def fake_build(name, cfg, **kw):
        calls.append(name)
        scraper = MagicMock()
        scraper.search.return_value = [cand] if name == "annas_curl" else []
        return scraper

    with patch("endless_library.scrapers.registry.build", side_effect=fake_build):
        cands, last = _search_with_strategies(deps, bengali)

    # Bengali floor (40) is cleared by score ~50+, so only annas runs
    assert calls == ["annas_curl"]
    assert last == "annas_curl"
    assert len(cands) == 1
