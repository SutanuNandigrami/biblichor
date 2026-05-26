"""Mobilism shared authenticated session (Phase 6w.5b).

MobilismSession provides a class-level cached httpx client that is
logged into forum.mobilism.org. On first call it POSTs credentials to
the phpBB login endpoint; subsequent calls return the cached session.
The cache is invalidated after 24 hours or when a 401/redirect-to-login
is detected.

Credentials are passed in via the ScrapersCfg-like object:
    cfg.mobilism_username
    cfg.mobilism_password

Exceptions
----------
NotConfigured  -- raised when credentials are missing.
AuthFailed     -- raised when the login POST redirects back to the login page
                  (phpBB's way of signalling bad credentials).
"""

from __future__ import annotations

import logging
import threading
import time

from endless_library.scrapers.http_client import make_client

log = logging.getLogger(__name__)

_LOGIN_URL = "https://forum.mobilism.org/ucp.php?mode=login"
_SESSION_TTL = 24 * 3600  # seconds


from endless_library.scrapers.base import NotConfigured  # noqa: E402


class AuthFailed(Exception):
    """Raised when Mobilism login returns a redirect back to the login page."""


# Class-level singleton state
class MobilismSession:
    _session = None
    _expires_at: float = 0.0
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls, cfg):
        """Return the cached session, logging in if needed.

        Parameters
        ----------
        cfg:
            Object with ``mobilism_username`` and ``mobilism_password``
            string attributes (ScrapersCfg or a test stub).

        Raises
        ------
        NotConfigured
            If credentials are blank / missing.
        AuthFailed
            If the login POST redirects back to the login URL.
        """
        username = getattr(cfg, "mobilism_username", "") or ""
        password = getattr(cfg, "mobilism_password", "") or ""
        if not username or not password:
            raise NotConfigured(
                "Mobilism credentials not configured. "
                "Set mobilism_username + mobilism_password in ScrapersCfg."
            )

        with cls._lock:
            now = time.monotonic()
            if cls._session is not None and now < cls._expires_at:
                return cls._session

            # Need a fresh login
            client = make_client(timeout=30)
            resp = client.post(
                _LOGIN_URL,
                data={
                    "username": username,
                    "password": password,
                    "login": "Login",
                    "redirect": "./index.php",
                    "sid": "",
                },
                follow_redirects=True,
            )

            # phpBB redirects to the index on success; if the final URL is still
            # the login page, credentials were rejected.
            final_url = str(resp.url)
            if "ucp.php?mode=login" in final_url or "mode=login" in final_url:
                raise AuthFailed(
                    f"Mobilism login failed: redirected back to login page ({final_url})"
                )

            cls._session = client
            cls._expires_at = now + _SESSION_TTL
            log.info("mobilism: session established (expires in 24h)")
            return cls._session

    @classmethod
    def invalidate(cls) -> None:
        """Force re-login on the next call to get()."""
        with cls._lock:
            cls._session = None
            cls._expires_at = 0.0

    @classmethod
    def try_login(cls, cfg) -> tuple[bool, str | None]:
        """Attempt a login WITHOUT touching the singleton session.

        Safe entry-point for credential-validation endpoints: builds a fresh
        client, POSTs credentials, inspects the redirect, and discards the
        client on return.  The singleton is never read or written, so concurrent
        requests that call get() cannot pick up a test-credential session.

        Returns (True, None) on success; (False, error_message) on failure.
        """
        username = getattr(cfg, "mobilism_username", "") or ""
        password = getattr(cfg, "mobilism_password", "") or ""
        if not username or not password:
            return False, "Mobilism credentials not configured."
        try:
            client = make_client(timeout=30)
            resp = client.post(
                _LOGIN_URL,
                data={
                    "username": username,
                    "password": password,
                    "login": "Login",
                    "redirect": "./index.php",
                    "sid": "",
                },
                follow_redirects=True,
            )
            final_url = str(resp.url)
            if "ucp.php?mode=login" in final_url or "mode=login" in final_url:
                return False, f"Login failed: redirected back to login page ({final_url})"
            return True, None
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"


def _reset_session_for_tests() -> None:
    """Test-only hook: clear the class-level MobilismSession singleton.

    Must not be called from production code paths. Named with the
    _for_tests suffix to make its test-only scope unambiguous
    (ultrareview I10).
    """
    with MobilismSession._lock:
        MobilismSession._session = None
        MobilismSession._expires_at = 0.0
