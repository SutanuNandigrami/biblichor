"""Phase 6s.5 tests — Z-Library SingleLogin + browser cookie upload."""

from __future__ import annotations

import httpx
import respx

from endless_library.domain.models import SearchQuery


def _sq(title: str) -> SearchQuery:
    return SearchQuery(
        title=title,
        author=None,
        isbn13=None,
        format_priority=("epub", "azw3", "mobi", "pdf"),
        language="",
    )


# ============ ZlibSingleLogin: no creds -> empty ============


def test_zlib_returns_empty_without_creds(tmp_path):
    """If no Z-Library credentials are saved, the scraper silently
    returns empty (the chain falls through to other strategies)."""
    from endless_library.db.schema import init_db
    from endless_library.scrapers.zlib_singlelogin import ZlibSingleLogin

    db = tmp_path / "library.db"
    init_db(db)
    # No secrets seeded
    z = ZlibSingleLogin(cfg=None, db_path=db)
    assert list(z.search(_sq("Pride and Prejudice"))) == []


# ============ ZlibSingleLogin: login + search ============


@respx.mock(assert_all_called=False)
def test_zlib_logs_in_and_caches_personal_domain(respx_mock, tmp_path):
    """When creds are stored, the scraper logs in via SingleLogin
    and persists the returned personalDomain into the secrets store."""
    respx_mock.post("https://singlelogin.re/rpc.php").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": {
                    "validationError": False,
                    "personalDomain": "https://abc123.personal.z-library.bz",
                }
            },
        )
    )
    respx_mock.get("https://abc123.personal.z-library.bz/s/Pride%20and%20Prejudice").mock(
        return_value=httpx.Response(
            200,
            text="""
            <html><body>
              <div class="book-card">
                <h3><a href="/book/123">Pride and Prejudice</a></h3>
                <div class="authors">Jane Austen</div>
                <div class="property_format">EPUB</div>
              </div>
            </body></html>
            """,
        )
    )

    from endless_library.bookorbit.service import BookOrbitService
    from endless_library.db.schema import init_db
    from endless_library.scrapers.zlib_singlelogin import ZlibSingleLogin

    db = tmp_path / "library.db"
    init_db(db)
    # Plant a fake recovery key so the secrets store has something
    secrets_dir = db.parent / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "restore.key").write_bytes(b"# public key: age1xyz\nAGE-SECRET-KEY-1ABC\n")
    # Plant zlib creds via the service
    from types import SimpleNamespace

    cfg_stub = SimpleNamespace(
        bookorbit=SimpleNamespace(enabled=False, url="", library_root="", library_id=""),
        general=SimpleNamespace(books_dir=str(db.parent / "books")),
    )
    svc = BookOrbitService(
        cfg=cfg_stub,
        db_path=db,
        restore_key_path=secrets_dir / "restore.key",
    )
    svc.store_zlib_creds("alice@example.com", "swordfish")

    z = ZlibSingleLogin(cfg=None, db_path=db)
    cands = z.search(_sq("Pride and Prejudice"))
    assert len(cands) >= 1
    assert cands[0].provider == "zlib"
    assert cands[0].title == "Pride and Prejudice"

    # personal_domain is now cached
    assert svc.get_secret_value("zlib.personal_domain") == "https://abc123.personal.z-library.bz"


# ============ Browser cookies upload (parsing only) ============


def test_browser_cookies_parsing(tmp_path):
    """The cookie-jar upload parses Netscape-format cookies.txt and
    groups by domain. This test exercises the parsing path directly
    (the SPA endpoint is a thin wrapper)."""
    import http.cookiejar

    cookies_file = tmp_path / "cookies.txt"
    cookies_file.write_text(
        "# Netscape HTTP Cookie File\n"
        ".singlelogin.re\tTRUE\t/\tTRUE\t9999999999\tsessionid\tabc\n"
        ".zlibrary.bz\tTRUE\t/\tTRUE\t9999999999\ttok\txyz\n"
    )
    jar = http.cookiejar.MozillaCookieJar(str(cookies_file))
    jar.load(ignore_discard=True, ignore_expires=True)
    by_domain: dict[str, list[tuple[str, str]]] = {}
    for c in jar:
        by_domain.setdefault(c.domain.lstrip("."), []).append((c.name, c.value))
    assert "singlelogin.re" in by_domain
    assert ("sessionid", "abc") in by_domain["singlelogin.re"]
    assert ("tok", "xyz") in by_domain["zlibrary.bz"]
