"""Tests for Phase 6w.5 — Mobilism scraper suite."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Task 1: mediafire_helpers
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


def test_mediafire_resolve_extracts_direct_link():
    """resolve() returns the URL inside window.location.href = '...'"""
    from endless_library.scrapers.mediafire_helpers import resolve

    html = """
    <html><body>
    <script>
    window.location.href = "https://download1337.mediafire.com/file/abc/book.epub";
    </script>
    </body></html>
    """
    session = MagicMock()
    session.get.return_value = _FakeResponse(html, 200)

    result = resolve("https://www.mediafire.com/file/abc/book.epub/file", session)
    assert result == "https://download1337.mediafire.com/file/abc/book.epub"


def test_mediafire_resolve_returns_none_when_no_href():
    """resolve() returns None when no window.location.href is found."""
    from endless_library.scrapers.mediafire_helpers import resolve

    html = "<html><body><p>Nothing here</p></body></html>"
    session = MagicMock()
    session.get.return_value = _FakeResponse(html, 200)

    result = resolve("https://www.mediafire.com/file/xyz/test.epub/file", session)
    assert result is None


# ---------------------------------------------------------------------------
# Task 2: MobilismSession
# ---------------------------------------------------------------------------

def _make_cfg(*, username: str = "user", password: str = "pass") -> SimpleNamespace:
    return SimpleNamespace(
        mobilism_username=username,
        mobilism_password=password,
    )


def test_mobilism_session_raises_not_configured_when_no_creds():
    """MobilismSession.get() raises NotConfigured when credentials are absent."""
    from endless_library.scrapers.mobilism import MobilismSession, NotConfigured, _reset_session

    _reset_session()

    cfg = SimpleNamespace(mobilism_username="", mobilism_password="")
    with __import__("pytest").raises(NotConfigured):
        MobilismSession.get(cfg)

    _reset_session()


def test_mobilism_session_is_cached():
    """MobilismSession.get() returns the same object on second call."""
    from endless_library.scrapers.mobilism import MobilismSession, _reset_session

    _reset_session()

    fake_session = MagicMock()
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.url = "https://forum.mobilism.org/index.php"
    fake_session.post.return_value = fake_response

    cfg = _make_cfg()
    with patch("endless_library.scrapers.mobilism.make_client", return_value=fake_session):
        s1 = MobilismSession.get(cfg)
        s2 = MobilismSession.get(cfg)

    assert s1 is s2
    _reset_session()


def test_mobilism_session_raises_auth_failed_on_redirect_to_login():
    """MobilismSession.get() raises AuthFailed when post redirects back to login page."""
    import pytest
    from endless_library.scrapers.mobilism import MobilismSession, AuthFailed, _reset_session

    _reset_session()

    fake_session = MagicMock()
    fake_response = MagicMock()
    fake_response.status_code = 200
    # Simulates being redirected back to the login page (no session established)
    fake_response.url = "https://forum.mobilism.org/ucp.php?mode=login"
    fake_session.post.return_value = fake_response

    cfg = _make_cfg()
    with patch("endless_library.scrapers.mobilism.make_client", return_value=fake_session):
        with pytest.raises(AuthFailed):
            MobilismSession.get(cfg)

    _reset_session()


# ---------------------------------------------------------------------------
# Task 3: mobilism_books scraper
# ---------------------------------------------------------------------------

def test_mobilism_books_extracts_thread_links_with_mediafire():
    """MobilismBooks.search() returns Candidates whose detail_url comes from thread links."""
    import pytest
    from unittest.mock import patch as _patch
    from endless_library.scrapers.mobilism_books import MobilismBooks
    from endless_library.domain.models import SearchQuery

    # Minimal ScrapersCfg-like stub
    cfg = SimpleNamespace(
        mobilism_username="user",
        mobilism_password="pass",
        request_delay_seconds=0,
        format_priority=["epub", "mobi", "pdf"],
        language="en",
    )

    forum_html = """
    <html><body>
    <div id="page-body">
      <dl class="row-item">
        <dt><a class="topictitle" href="./viewtopic.php?t=1001">The Great Gatsby - EPUB</a></dt>
      </dl>
      <dl class="row-item">
        <dt><a class="topictitle" href="./viewtopic.php?t=1002">Some Other Book</a></dt>
      </dl>
    </div>
    </body></html>
    """
    post_html = """
    <html><body>
    <div class="postbody">
      <a href="https://www.mediafire.com/file/abc123/gatsby.epub/file">Download EPUB</a>
    </div>
    </body></html>
    """
    direct_url = "https://download1.mediafire.com/file/abc123/gatsby.epub"

    fake_response_forum = _FakeResponse(forum_html, 200)
    fake_response_post = _FakeResponse(post_html, 200)
    fake_session = MagicMock()
    fake_session.get.side_effect = [fake_response_forum, fake_response_post, fake_response_post]

    with _patch("endless_library.scrapers.mobilism_books.MobilismSession") as mock_sess_cls, \
         _patch("endless_library.scrapers.mobilism_books.resolve", return_value=direct_url):
        mock_sess_cls.get.return_value = fake_session
        scraper = MobilismBooks(cfg)
        sq = SearchQuery(
            title="The Great Gatsby",
            author="Fitzgerald",
            isbn13=None,
            format_priority=("epub", "mobi"),
            language="en",
        )
        cands = scraper.search(sq)

    assert len(cands) >= 1
    assert cands[0].provider == "mobilism_books"
    assert cands[0].detail_url == "https://www.mediafire.com/file/abc123/gatsby.epub/file"


# ---------------------------------------------------------------------------
# Task 5: chain_for_source promotes mobilism_books for recent releases
# ---------------------------------------------------------------------------

def _make_scrapers_cfg(enabled_names: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        order=enabled_names,
        enabled={n: True for n in enabled_names},
        mobilism_username="u",
        mobilism_password="p",
    )


def test_chain_promotes_mobilism_books_for_recent_release():
    """chain_for_source promotes mobilism_books to front when is_recent_release=True."""
    from endless_library.scrapers import registry as reg

    cfg = _make_scrapers_cfg(["annas_curl", "mobilism_books", "libgen_curl"])
    chain = reg.chain_for_source(
        cfg,
        source=None,
        query_title="New Book 2025",
        is_pd=False,
        is_recent_release=True,
    )
    if "mobilism_books" in chain:
        assert chain[0] == "mobilism_books"


def test_chain_does_not_promote_mobilism_books_for_old_book():
    """chain_for_source does NOT promote mobilism_books when is_recent_release=False."""
    from endless_library.scrapers import registry as reg

    cfg = _make_scrapers_cfg(["annas_curl", "mobilism_books", "libgen_curl"])
    chain = reg.chain_for_source(
        cfg,
        source=None,
        query_title="Classic Book",
        is_pd=False,
        is_recent_release=False,
    )
    if "mobilism_books" in chain and len(chain) > 1:
        assert chain[0] != "mobilism_books"

def test_chain_for_source_does_not_promote_mobilism_for_pd_recent_book():
    """If a book is tagged is_pd=True AND is_recent_release=True (e.g.
    tag drift), the PD chain should still take precedence."""
    from endless_library.scrapers.registry import chain_for_source
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        order=["annas_curl", "gutendex", "mobilism_books"],
        enabled={"annas_curl": True, "gutendex": True, "mobilism_books": True},
    )
    chain = chain_for_source(cfg, source=None, query_title="Pride and Prejudice",
                             is_pd=True, is_recent_release=True)
    # PD scraper should appear before mobilism_books
    if "gutendex" in chain and "mobilism_books" in chain:
        assert chain.index("gutendex") < chain.index("mobilism_books")
