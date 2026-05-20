"""Phase 6s.4 tests — centralized metadata helpers."""

from __future__ import annotations

import httpx
import respx

# ============ metadata_cache table ============


def test_metadata_cache_table_exists(tmp_path):
    from endless_library.db.schema import connect, init_db

    db = tmp_path / "library.db"
    init_db(db)
    with connect(db) as conn:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert "metadata_cache" in tables


# ============ resolve_by_isbn ============


@respx.mock(assert_all_called=False)
def test_resolve_by_isbn_returns_metadata(respx_mock, tmp_path):
    respx_mock.get("https://openlibrary.org/api/books").mock(
        return_value=httpx.Response(
            200,
            json={
                "ISBN:9780525559474": {
                    "title": "The Midnight Library",
                    "authors": [{"name": "Matt Haig"}],
                    "publish_date": "2020",
                    "subjects": [{"name": "Fiction"}],
                    "cover": {"large": "https://covers.openlibrary.org/b/id/x-L.jpg"},
                    "identifiers": {"isbn_13": ["9780525559474"]},
                }
            },
        )
    )

    from endless_library.db.schema import init_db
    from endless_library.metadata.openlibrary import resolve_by_isbn

    db = tmp_path / "library.db"
    init_db(db)
    meta = resolve_by_isbn("9780525559474", db_path=db)
    assert meta is not None
    assert meta["title"] == "The Midnight Library"
    assert "Matt Haig" in meta["authors"]
    assert meta["cover_url"].startswith("https://covers.openlibrary.org")


@respx.mock(assert_all_called=False)
def test_resolve_by_isbn_uses_cache_on_second_call(respx_mock, tmp_path):
    """Second call must hit the metadata_cache table, not OpenLibrary."""
    route = respx_mock.get("https://openlibrary.org/api/books").mock(
        return_value=httpx.Response(
            200,
            json={"ISBN:9780525559474": {"title": "Cached Book", "authors": []}},
        )
    )

    from endless_library.db.schema import init_db
    from endless_library.metadata.openlibrary import resolve_by_isbn

    db = tmp_path / "library.db"
    init_db(db)
    first = resolve_by_isbn("9780525559474", db_path=db)
    second = resolve_by_isbn("9780525559474", db_path=db)
    assert first == second
    assert route.call_count == 1


@respx.mock(assert_all_called=False)
def test_resolve_by_isbn_empty_input_returns_none(respx_mock, tmp_path):
    from endless_library.metadata.openlibrary import resolve_by_isbn

    assert resolve_by_isbn("", db_path=None) is None


@respx.mock(assert_all_called=False)
def test_resolve_by_isbn_unknown_returns_none(respx_mock, tmp_path):
    respx_mock.get("https://openlibrary.org/api/books").mock(
        return_value=httpx.Response(200, json={})
    )
    from endless_library.metadata.openlibrary import resolve_by_isbn

    assert resolve_by_isbn("0000000000000", db_path=None) is None


# ============ resolve_by_title_author ============


@respx.mock(assert_all_called=False)
def test_resolve_by_title_author_returns_top_match(respx_mock, tmp_path):
    respx_mock.get("https://openlibrary.org/search.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "docs": [
                    {
                        "title": "The Midnight Library",
                        "author_name": ["Matt Haig"],
                        "first_publish_year": 2020,
                        "isbn": ["9780525559474"],
                        "key": "/works/OL27418325W",
                    }
                ]
            },
        )
    )

    from endless_library.metadata.openlibrary import resolve_by_title_author

    meta = resolve_by_title_author("The Midnight Library", "Matt Haig", db_path=None)
    assert meta is not None
    assert meta["first_publish_year"] == 2020
    assert meta["isbn"] == "9780525559474"


# ============ resolve_by_asin / asin_to_isbn facade ============


@respx.mock(assert_all_called=False)
def test_resolve_by_asin_returns_isbn(respx_mock):
    respx_mock.get("https://openlibrary.org/api/books").mock(
        return_value=httpx.Response(
            200,
            json={"ASIN:B08FC6MR62": {"identifiers": {"isbn_13": ["9780525559474"]}}},
        )
    )
    from endless_library.metadata.openlibrary import resolve_by_asin

    assert resolve_by_asin("B08FC6MR62", db_path=None) == "9780525559474"


@respx.mock(assert_all_called=False)
def test_asin_to_isbn_facade_calls_openlibrary(respx_mock):
    respx_mock.get("https://openlibrary.org/api/books").mock(
        return_value=httpx.Response(
            200,
            json={"ASIN:B08X": {"identifiers": {"isbn_13": ["9781111111111"]}}},
        )
    )
    from endless_library.metadata.asin_resolver import asin_to_isbn

    assert asin_to_isbn("B08X", db_path=None) == "9781111111111"


def test_asin_to_isbn_with_empty_returns_none():
    from endless_library.metadata.asin_resolver import asin_to_isbn

    assert asin_to_isbn("", db_path=None) is None
