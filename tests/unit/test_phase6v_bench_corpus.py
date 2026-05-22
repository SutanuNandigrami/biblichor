"""Phase 6v.3: per-scraper bench corpus filter.

Scrapers benched against the right corpus stop reporting 0% just
because the bench feeds them queries they were never built for
(e.g. kindlebangla_curl tested with "Sapiens"). Tags on queries +
`corpus_tags` mapping in queries.yaml drives the filter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from endless_library.bench import (
    BenchQuery,
    load_corpus_tags,
    load_queries,
    queries_for_scraper,
)


BENCH_FILE = Path(__file__).resolve().parent.parent.parent / "bench" / "queries.yaml"


def test_bundled_yaml_has_three_corpora():
    """Sanity guard: the bundled queries.yaml carries modern English,
    public-domain English, AND Bengali queries — drop any of these and
    a whole class of scrapers loses its bench coverage."""
    qs, _ = load_queries(BENCH_FILE)
    tag_sets = [set(q.tags) for q in qs]
    assert any({"modern"} <= t for t in tag_sets), "missing modern-English corpus"
    assert any({"pd"} <= t for t in tag_sets), "missing PD corpus"
    assert any({"bn"} <= t and {"kindlebangla"} <= t for t in tag_sets), (
        "missing Bengali/kindlebangla corpus"
    )


def test_bundled_yaml_corpus_tags_route_specialised_scrapers():
    """KindleBangla, Gutendex, Standard Ebooks, OAPEN/DOAB, Wikisource
    are listed in corpus_tags so they only see queries they can answer.
    """
    ct = load_corpus_tags(BENCH_FILE)
    assert "kindlebangla_curl" in ct
    assert "kindlebangla" in ct["kindlebangla_curl"]
    for pd_scraper in ("gutendex", "standard_ebooks", "oapen_doab", "wikisource"):
        assert pd_scraper in ct, f"{pd_scraper} missing from corpus_tags"
        assert "pd" in ct[pd_scraper]


def test_queries_for_scraper_filters_by_tag():
    qs = [
        BenchQuery("Sapiens", "Harari", "9780062316097", "en", tags=("en", "modern")),
        BenchQuery("Pride and Prejudice", "Austen", "", "en", tags=("en", "pd")),
        BenchQuery("হিমু", "Humayun Ahmed", "", "bn", tags=("bn", "kindlebangla")),
    ]
    corpus = {
        "kindlebangla_curl": frozenset({"kindlebangla"}),
        "gutendex": frozenset({"pd"}),
    }
    kb = queries_for_scraper(qs, "kindlebangla_curl", corpus)
    pd = queries_for_scraper(qs, "gutendex", corpus)
    general = queries_for_scraper(qs, "annas_curl", corpus)
    assert [q.title for q in kb] == ["হিমু"]
    assert [q.title for q in pd] == ["Pride and Prejudice"]
    assert len(general) == 3  # absent from corpus_tags => unfiltered


def test_queries_for_scraper_empty_when_no_tag_intersects():
    """Specialised scraper + a corpus that has nothing it can use
    yields an empty list — drives run_bench's 'skip this scraper'
    branch, so the dashboard isn't littered with bogus 0% rows."""
    qs = [BenchQuery("Sapiens", "Harari", "", "en", tags=("en", "modern"))]
    corpus = {"kindlebangla_curl": frozenset({"kindlebangla"})}
    assert queries_for_scraper(qs, "kindlebangla_curl", corpus) == []


def test_legacy_query_without_tags_treated_as_general(tmp_path: Path):
    """An older queries.yaml that pre-dates Phase 6v.3 has no `tags`
    field. Such queries must still be visible to general-purpose
    scrapers (which would otherwise see 0 queries and skip)."""
    yaml_path = tmp_path / "queries.yaml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "queries": [
                    {
                        "title": "Untagged Classic",
                        "author": "Author",
                        "isbn13": "",
                        "language": "en",
                    }
                ],
                "quick_indices": [0],
            }
        ),
        encoding="utf-8",
    )
    qs, _ = load_queries(yaml_path)
    assert qs[0].tags == ()
    # General-purpose scraper (not in corpus_tags) still gets it.
    assert len(queries_for_scraper(qs, "annas_curl", {})) == 1
    # Specialised scraper with no intersection (untagged) gets nothing.
    assert queries_for_scraper(
        qs, "kindlebangla_curl", {"kindlebangla_curl": frozenset({"kindlebangla"})}
    ) == []


def test_load_corpus_tags_missing_section_is_empty(tmp_path: Path):
    """A queries.yaml that doesn't define corpus_tags returns an
    empty dict — every scraper gets the full corpus."""
    p = tmp_path / "q.yaml"
    p.write_text("queries: []\n", encoding="utf-8")
    assert load_corpus_tags(p) == {}


def test_load_corpus_tags_returns_frozenset():
    """Frozen so callers can't mutate the cached corpus by accident."""
    ct = load_corpus_tags(BENCH_FILE)
    for v in ct.values():
        assert isinstance(v, frozenset)


def test_quick_indices_touch_three_corpora():
    """quick_indices is the `bench --quick` subset. With three corpora
    in play, the quick mode should sample from each so even a fast
    run isn't English-only."""
    qs, quick = load_queries(BENCH_FILE)
    sampled_tags: set[str] = set()
    for i in quick:
        if 0 <= i < len(qs):
            for t in qs[i].tags:
                sampled_tags.add(t)
    assert "modern" in sampled_tags
    assert "pd" in sampled_tags
    assert "kindlebangla" in sampled_tags
