# tests/unit/test_phase6w1_http_foundation.py
def test_curl_cffi_imports():
    from curl_cffi import requests as cffi_requests
    assert cffi_requests.Session is not None

import hashlib

def test_solve_anubis_finds_valid_nonce_at_difficulty_8():
    from endless_library.scrapers.anubis import solve_anubis
    challenge = "abc"
    n = solve_anubis(challenge, 8)
    h = hashlib.sha256(f"{challenge}{n}".encode()).digest()
    assert h[0] == 0   # 8 leading zero bits


def test_solve_anubis_handles_non_byte_aligned_difficulty():
    from endless_library.scrapers.anubis import solve_anubis
    challenge = "test"
    n = solve_anubis(challenge, 12)
    h = hashlib.sha256(f"{challenge}{n}".encode()).digest()
    assert h[0] == 0
    assert (h[1] & 0xF0) == 0   # next 4 bits also zero


def test_solve_anubis_returns_int():
    from endless_library.scrapers.anubis import solve_anubis
    n = solve_anubis("x", 4)
    assert isinstance(n, int) and n >= 0


def test_solve_anubis_zero_difficulty_returns_zero():
    from endless_library.scrapers.anubis import solve_anubis
    assert solve_anubis("any", 0) == 0


def test_make_client_returns_cffi_session():
    from endless_library.scrapers.http_client import make_client
    from curl_cffi.requests import Session
    c = make_client()
    assert isinstance(c, Session)


def test_make_client_accepts_proxies():
    from endless_library.scrapers.http_client import make_client
    c = make_client(proxies={"all": "socks5h://localhost:9050"})
    # curl-cffi stores proxies in session.proxies
    assert "all" in c.proxies


def test_anubis_middleware_only_triggers_on_signature(monkeypatch):
    """If the response HTML doesn't match the Anubis fingerprint,
    middleware must be a no-op."""
    from endless_library.scrapers.http_client import _is_anubis_response
    class _R:
        status_code = 200
        text = "<html><body>Hello</body></html>"
        headers = {"content-type": "text/html"}
    assert _is_anubis_response(_R()) is False


def test_anubis_middleware_detects_signature():
    from endless_library.scrapers.http_client import _is_anubis_response
    class _R:
        status_code = 200
        text = '<html><head><meta name="anubis-challenge" content="abc"></head></html>'
        headers = {"content-type": "text/html"}
    assert _is_anubis_response(_R()) is True


def test_anubis_middleware_solves_and_retries(monkeypatch):
    """End-to-end: a fake session whose first GET returns Anubis, second
    GET returns a normal 200. Middleware should solve + re-issue.
    """
    from endless_library.scrapers.http_client import _solve_and_get_cookie
    challenge_html = (
        '<html><head>'
        '<meta name="anubis-challenge" content="abc">'
        '<meta name="anubis-difficulty" content="4">'
        '<meta name="anubis-action" content="/anubis/pass">'
        '</head></html>'
    )
    posted = {}
    class _Sess:
        def post(self, url, data=None, **kw):
            posted["url"] = url
            posted["data"] = data
            class _R:
                status_code = 200
                cookies = {"techaro-anubis-auth": "JWT"}
            return _R()
    cookie = _solve_and_get_cookie(challenge_html, "https://example.com/page", _Sess())
    assert cookie == "JWT"
    assert posted["url"].endswith("/anubis/pass")
    assert "nonce" in posted["data"] and "challenge" in posted["data"]
