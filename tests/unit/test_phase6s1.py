"""Phase 6s.1 tests — bench fix + zero-config acquisition scrapers."""

from __future__ import annotations

import httpx
import respx

from endless_library.domain.models import Candidate, SearchQuery


def _sq(title: str, author: str | None = None, language: str = "") -> SearchQuery:
    return SearchQuery(
        title=title,
        author=author,
        isbn13=None,
        format_priority=("epub", "azw3", "mobi", "pdf"),
        language=language,
    )


# ============ Task 1: bench fix ============


def test_bench_load_queries_resolves_default_path():
    from endless_library.bench import load_queries

    queries, quick_idx = load_queries()
    assert len(queries) > 0
    assert isinstance(quick_idx, list)
    assert all(0 <= i < len(queries) for i in quick_idx)


# ============ Task 2: schema migration ============


def test_books_schema_has_pd_columns(tmp_path):
    from endless_library.db.schema import connect, init_db

    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
    assert "pub_year" in cols
    assert "is_public_domain" in cols


# ============ Task 3: Gutendex ============


@respx.mock(base_url="https://gutendex.com", assert_all_called=False)
def test_gutendex_search_returns_candidates(respx_mock):
    respx_mock.get("/books").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 1,
                "results": [
                    {
                        "id": 1342,
                        "title": "Pride and Prejudice",
                        "authors": [{"name": "Austen, Jane"}],
                        "languages": ["en"],
                        "formats": {
                            "application/epub+zip": "https://www.gutenberg.org/ebooks/1342.epub.images",
                            "text/plain; charset=utf-8": "https://www.gutenberg.org/files/1342/1342-0.txt",
                        },
                    }
                ],
            },
        )
    )
    from endless_library.scrapers.gutendex import Gutendex

    g = Gutendex(cfg=None)
    cands = g.search(_sq("Pride and Prejudice", "Jane Austen"))
    assert len(cands) == 1
    assert cands[0].title == "Pride and Prejudice"
    assert cands[0].format == "epub"
    assert "gutenberg" in cands[0].detail_url
    assert cands[0].provider == "gutendex"


@respx.mock(base_url="https://gutendex.com", assert_all_called=False)
def test_gutendex_no_results_returns_empty(respx_mock):
    respx_mock.get("/books").mock(return_value=httpx.Response(200, json={"results": []}))
    from endless_library.scrapers.gutendex import Gutendex

    g = Gutendex(cfg=None)
    assert g.search(_sq("nonexistent xyzzy")) == []


def test_gutendex_resolve_cdn_returns_handle():
    from endless_library.scrapers.gutendex import Gutendex

    g = Gutendex(cfg=None)
    c = Candidate(
        provider="gutendex",
        md5=None,
        title="x",
        author="y",
        language="en",
        format="epub",
        filesize_bytes=None,
        year=None,
        publisher=None,
        edition_hints="",
        detail_url="https://www.gutenberg.org/ebooks/1.epub",
    )
    handle = g.resolve_cdn(c)
    assert handle is not None
    assert handle.url == c.detail_url


# ============ Task 4: Standard Ebooks ============


@respx.mock(base_url="https://standardebooks.org", assert_all_called=False)
def test_standard_ebooks_search_returns_candidates(respx_mock):
    atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">
  <entry>
    <title>Pride and Prejudice</title>
    <author><name>Jane Austen</name></author>
    <link rel="http://opds-spec.org/acquisition" type="application/epub+zip"
          href="/ebooks/jane-austen/pride-and-prejudice/downloads/jane-austen_pride-and-prejudice.epub"/>
    <dc:language>en-GB</dc:language>
  </entry>
</feed>"""
    respx_mock.get("/opds/all").mock(return_value=httpx.Response(200, text=atom))

    from endless_library.scrapers.standard_ebooks import StandardEbooks

    se = StandardEbooks(cfg=None)
    cands = se.search(_sq("Pride and Prejudice", "Jane Austen"))
    assert len(cands) >= 1
    assert cands[0].format == "epub"
    assert "standardebooks.org" in cands[0].detail_url
    assert cands[0].provider == "standard_ebooks"


# ============ Task 5: OAPEN/DOAB ============


@respx.mock(assert_all_called=False)
def test_oapen_doab_search_aggregates_both_apis(respx_mock):
    respx_mock.get("https://library.oapen.org/rest/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "Open Science",
                    "metadata": [
                        {"key": "dc.title", "value": "Open Science"},
                        {"key": "dc.creator", "value": "Alice Smith"},
                        {"key": "dc.identifier.doi", "value": "10.1234/abc"},
                    ],
                    "bitstreams": [
                        {
                            "format": "application/pdf",
                            "retrieveLink": "/bitstream/handle/x/y.pdf",
                        }
                    ],
                }
            ],
        )
    )
    respx_mock.get("https://directory.doabooks.org/rest/search").mock(
        return_value=httpx.Response(200, json=[])
    )
    from endless_library.scrapers.oapen_doab import OapenDoab

    s = OapenDoab(cfg=None)
    cands = s.search(_sq("Open Science"))
    assert len(cands) == 1
    assert cands[0].format == "pdf"
    assert "oapen.org" in cands[0].detail_url
    assert cands[0].provider == "oapen"


# ============ Task 6: Wikisource ============


@respx.mock(assert_all_called=False)
def test_wikisource_resolves_via_wikidata_then_ws_export(respx_mock):
    respx_mock.get("https://query.wikidata.org/sparql").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "work": {"value": "http://www.wikidata.org/entity/Q170583"},
                            "wikisourcePage": {
                                "value": "https://en.wikisource.org/wiki/Pride_and_Prejudice"
                            },
                        }
                    ]
                }
            },
        )
    )
    from endless_library.scrapers.wikisource import Wikisource

    s = Wikisource(cfg=None)
    cands = s.search(_sq("Pride and Prejudice", "Jane Austen"))
    assert len(cands) >= 1
    assert cands[0].format == "epub"
    assert "ws-export.wmcloud.org" in cands[0].detail_url
    assert cands[0].provider == "wikisource"


# ============ Task 7: PD pre-chain hook ============


def test_pd_book_promotes_standard_ebooks_first():
    from endless_library.config import ScrapersCfg
    from endless_library.scrapers.registry import pd_aware_order

    cfg = ScrapersCfg(
        order=[
            "annas_curl",
            "welib_curl",
            "libgen_curl",
            "gutendex",
            "standard_ebooks",
            "oapen_doab",
            "wikisource",
        ],
        enabled={
            "annas_curl": True,
            "welib_curl": True,
            "libgen_curl": True,
            "gutendex": True,
            "standard_ebooks": True,
            "oapen_doab": True,
            "wikisource": True,
        },
    )
    promoted = pd_aware_order(cfg, query_title="Pride and Prejudice", is_pd=True)
    assert promoted[:4] == [
        "standard_ebooks",
        "gutendex",
        "wikisource",
        "oapen_doab",
    ]
    assert "annas_curl" in promoted[4:]


def test_non_pd_book_uses_existing_order():
    from endless_library.config import ScrapersCfg
    from endless_library.scrapers.registry import pd_aware_order

    cfg = ScrapersCfg(
        order=["annas_curl", "welib_curl"],
        enabled={"annas_curl": True, "welib_curl": True},
    )
    result = pd_aware_order(cfg, query_title="Atomic Habits", is_pd=False)
    assert result == ["annas_curl", "welib_curl"]
