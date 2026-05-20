"""Phase 6s.3 tests — new reading-list sources."""

from __future__ import annotations

import re

import httpx
import respx

# ============ NYT Best Sellers ============


@respx.mock(assert_all_called=False)
def test_nyt_bestsellers_returns_book_refs(respx_mock):
    respx_mock.get(
        re.compile(r"https://api\.nytimes\.com/svc/books/v3/lists/current/.*\.json.*")
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "books": [
                        {
                            "primary_isbn13": "9780525559474",
                            "title": "The Midnight Library",
                            "author": "Matt Haig",
                        }
                    ]
                }
            },
        )
    )
    from endless_library.sources.nyt_bestsellers import NYTBestSellers

    s = NYTBestSellers()
    refs = list(s.list_to_read(identifier="hardcover-fiction", token="API_KEY"))
    assert len(refs) == 1
    assert refs[0].isbn13 == "9780525559474"
    assert refs[0].source == "nyt"


def test_nyt_returns_empty_without_token():
    from endless_library.sources.nyt_bestsellers import NYTBestSellers

    s = NYTBestSellers()
    refs = list(s.list_to_read(identifier="hardcover-fiction", token=None))
    assert refs == []


# ============ StoryGraph ============


@respx.mock(assert_all_called=False)
def test_storygraph_parses_to_read_shelf(respx_mock):
    html = """
    <html><body>
      <div>
        <h3><a href="/books/the-midnight-library">The Midnight Library</a></h3>
        <p>Matt Haig</p>
      </div>
      <div>
        <h3><a href="/books/atomic-habits">Atomic Habits</a></h3>
        <p>James Clear</p>
      </div>
    </body></html>
    """
    respx_mock.get(re.compile(r"https://app\.thestorygraph\.com/.*")).mock(
        return_value=httpx.Response(200, text=html)
    )
    from endless_library.sources.storygraph import StoryGraph

    s = StoryGraph()
    refs = list(s.list_to_read(identifier="testuser", token=None))
    titles = {r.title for r in refs}
    assert "The Midnight Library" in titles
    assert "Atomic Habits" in titles
    for r in refs:
        assert r.source == "storygraph"


def test_storygraph_returns_empty_for_blank_identifier():
    from endless_library.sources.storygraph import StoryGraph

    assert list(StoryGraph().list_to_read(identifier="", token=None)) == []


# ============ BookWyrm ============


@respx.mock(assert_all_called=False)
def test_bookwyrm_parses_activitypub_outbox(respx_mock):
    respx_mock.get("https://bookwyrm.social/user/alice/books/to-read.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "orderedItems": [
                    {
                        "book": {
                            "title": "The Midnight Library",
                            "authors": [{"name": "Matt Haig"}],
                            "isbn13": "9780525559474",
                            "openlibraryKey": "OL27418325M",
                        }
                    }
                ]
            },
        )
    )
    from endless_library.sources.bookwyrm import BookWyrm

    s = BookWyrm()
    refs = list(s.list_to_read(identifier="bookwyrm.social:alice", token=None))
    assert len(refs) == 1
    assert refs[0].title == "The Midnight Library"
    assert refs[0].isbn13 == "9780525559474"
    assert refs[0].source == "bookwyrm"


def test_bookwyrm_rejects_bad_identifier():
    from endless_library.sources.bookwyrm import BookWyrm

    refs = list(BookWyrm().list_to_read(identifier="just-a-name", token=None))
    assert refs == []


# ============ Wikidata author ============


@respx.mock(assert_all_called=False)
def test_wikidata_author_bibliography(respx_mock):
    respx_mock.get("https://query.wikidata.org/sparql").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "work": {"value": "http://www.wikidata.org/entity/Q170583"},
                            "workLabel": {"value": "Pride and Prejudice"},
                            "isbn13": {"value": "9780141439518"},
                        }
                    ]
                }
            },
        )
    )
    from endless_library.sources.wikidata_author import WikidataAuthor

    refs = list(WikidataAuthor().list_to_read(identifier="Q36322", token=None))
    assert len(refs) == 1
    assert refs[0].title == "Pride and Prejudice"
    assert refs[0].isbn13 == "9780141439518"
    assert refs[0].source == "wikidata"


def test_wikidata_rejects_non_q_id():
    from endless_library.sources.wikidata_author import WikidataAuthor

    assert list(WikidataAuthor().list_to_read(identifier="Charles Dickens", token=None)) == []


# ============ Registry ============


def test_new_sources_registered():
    from endless_library.sources.registry import _SOURCES

    assert "nyt" in _SOURCES
    assert "storygraph" in _SOURCES
    assert "bookwyrm" in _SOURCES
    assert "wikidata" in _SOURCES
