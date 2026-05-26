"""Tests for ultrareview Important fixes I1 (MobilismSession.try_login)
and I2 (Anubis middleware wraps all HTTP methods).
Ultrareview commit batch 1.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# I1 -- MobilismSession.try_login: safe credential check without singleton
# ---------------------------------------------------------------------------


def _make_cfg(*, username: str = "user", password: str = "pass") -> SimpleNamespace:
    return SimpleNamespace(
        mobilism_username=username,
        mobilism_password=password,
    )


def test_try_login_returns_false_when_no_creds():
    """try_login returns (False, msg) when credentials are empty."""
    from endless_library.scrapers.mobilism import MobilismSession

    ok, err = MobilismSession.try_login(SimpleNamespace(mobilism_username="", mobilism_password=""))
    assert ok is False
    assert err is not None
    assert "not configured" in err.lower() or "credentials" in err.lower()


def test_try_login_does_not_touch_singleton_on_success():
    """try_login must not write the class-level singleton, even on success."""
    from endless_library.scrapers.mobilism import MobilismSession, _reset_session_for_tests

    _reset_session_for_tests()
    assert MobilismSession._session is None

    fake_session = MagicMock()
    fake_resp = MagicMock()
    fake_resp.url = "https://forum.mobilism.org/index.php"
    fake_session.post.return_value = fake_resp

    with patch("endless_library.scrapers.mobilism.make_client", return_value=fake_session):
        ok, err = MobilismSession.try_login(_make_cfg())

    assert ok is True
    assert err is None
    # Critical: singleton must still be None
    assert MobilismSession._session is None, "try_login must NOT write the singleton"
    _reset_session_for_tests()


def test_try_login_returns_false_on_redirect_to_login():
    """try_login returns (False, msg) when login redirects back to the login page."""
    from endless_library.scrapers.mobilism import MobilismSession, _reset_session_for_tests

    _reset_session_for_tests()

    fake_session = MagicMock()
    fake_resp = MagicMock()
    fake_resp.url = "https://forum.mobilism.org/ucp.php?mode=login"
    fake_session.post.return_value = fake_resp

    with patch("endless_library.scrapers.mobilism.make_client", return_value=fake_session):
        ok, err = MobilismSession.try_login(_make_cfg())

    assert ok is False
    assert err is not None
    assert MobilismSession._session is None
    _reset_session_for_tests()


def test_try_login_returns_false_on_network_error():
    """try_login returns (False, msg) when the network call raises."""
    from endless_library.scrapers.mobilism import MobilismSession, _reset_session_for_tests

    _reset_session_for_tests()

    fake_session = MagicMock()
    fake_session.post.side_effect = ConnectionError("simulated failure")

    with patch("endless_library.scrapers.mobilism.make_client", return_value=fake_session):
        ok, err = MobilismSession.try_login(_make_cfg())

    assert ok is False
    assert "ConnectionError" in (err or "")
    assert MobilismSession._session is None
    _reset_session_for_tests()


def test_try_login_does_not_corrupt_existing_singleton():
    """try_login with bad creds must not overwrite an existing valid singleton."""
    from endless_library.scrapers.mobilism import MobilismSession, _reset_session_for_tests

    _reset_session_for_tests()

    sentinel = MagicMock()
    MobilismSession._session = sentinel
    MobilismSession._expires_at = 9999999999.0

    fake_bad_session = MagicMock()
    fake_resp = MagicMock()
    fake_resp.url = "https://forum.mobilism.org/ucp.php?mode=login"
    fake_bad_session.post.return_value = fake_resp

    with patch("endless_library.scrapers.mobilism.make_client", return_value=fake_bad_session):
        ok, _err = MobilismSession.try_login(
            SimpleNamespace(mobilism_username="bad", mobilism_password="creds")
        )

    assert ok is False
    assert MobilismSession._session is sentinel, (
        "try_login must not overwrite an existing valid singleton"
    )
    _reset_session_for_tests()


# ---------------------------------------------------------------------------
# I2 -- Anubis middleware wraps get/post/head/put/delete
# ---------------------------------------------------------------------------


def test_anubis_middleware_wraps_all_http_methods():
    """_install_anubis_middleware must wrap all five HTTP methods."""
    from endless_library.scrapers.http_client import _install_anubis_middleware

    session = MagicMock()
    originals = {}
    for method in ("get", "post", "head", "put", "delete"):
        orig = MagicMock(name=f"orig_{method}")
        setattr(session, method, orig)
        originals[method] = orig

    _install_anubis_middleware(session)

    for method in ("get", "post", "head", "put", "delete"):
        wrapped = getattr(session, method)
        assert wrapped is not originals[method], (
            f"session.{method} was not replaced by the Anubis middleware"
        )


def test_anubis_middleware_wrapped_methods_are_callable():
    """All wrapped methods must be callable and pass through on non-Anubis responses."""
    from endless_library.scrapers.http_client import _install_anubis_middleware

    session = MagicMock()
    for method in ("get", "post", "head", "put", "delete"):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.headers = {"content-type": "application/json"}
        fake_resp.text = "ok"
        setattr(session, method, MagicMock(return_value=fake_resp))

    _install_anubis_middleware(session)

    for method in ("get", "post", "head", "put", "delete"):
        fn = getattr(session, method)
        result = fn("https://example.com/path")
        assert result is not None, f"{method}() returned None"


def test_anubis_middleware_injects_cached_cookie():
    """After a cookie is cached, it is injected in the next request."""
    import time

    from endless_library.scrapers.http_client import (
        _ANUBIS_COOKIE_CACHE,
        _install_anubis_middleware,
    )

    host = "anubis-test-xyz.example.com"
    _ANUBIS_COOKIE_CACHE[host] = ("jwt_abc", time.time() + 9999)

    captured_kw: dict = {}

    def fake_get(url, **kw):
        captured_kw.update(kw)
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.text = "ok"
        return resp

    session = MagicMock()
    session.get = fake_get
    for m in ("post", "head", "put", "delete"):
        setattr(session, m, MagicMock(return_value=MagicMock(
            status_code=200, text="", headers={})))

    _install_anubis_middleware(session)
    session.get(f"https://{host}/path")

    assert "cookies" in captured_kw, "cookies kw not injected"
    assert captured_kw["cookies"].get("techaro-anubis-auth") == "jwt_abc"

    del _ANUBIS_COOKIE_CACHE[host]


# ---------------------------------------------------------------------------
# I3 -- registry.py: SCRAPER_TO_OPEN_SLUM_SITE is not duplicated
# ---------------------------------------------------------------------------


def test_scraper_to_open_slum_site_not_duplicated():
    """SCRAPER_TO_OPEN_SLUM_SITE must be defined exactly once in registry.py."""
    from pathlib import Path
    src = Path("/home/ubuntu/endless-library/src/endless_library/scrapers/registry.py")
    count = src.read_text().count("SCRAPER_TO_OPEN_SLUM_SITE: dict")
    assert count == 1, (
        f"SCRAPER_TO_OPEN_SLUM_SITE is defined {count} times in registry.py (expected 1)"
    )
