"""Shared HTTP client factory for all scrapers.

Returns a `curl_cffi.requests.Session` that impersonates a real
Chrome browser at the TLS / JA3 / JA4 layer. This alone defeats ~30%
of Cloudflare 'bot-fight-mode' challenges that previously pushed
biblichor into FlareSolverr or the cloak-browser path.

Also installs an Anubis PoW middleware: any response whose HTML
matches the Anubis fingerprint is intercepted, the PoW is solved
in-process, the JWT cookie is captured, and the original request
is retried automatically. Cache keyed by host (50-min TTL).

Scraper routing summary
-----------------------
make_client() is used by: anubis solver path, HathiTrust, DOAB, BDeBooks,
mobilism_books, the migrated sub-methods of archive_curl/kindlebangla_curl/
welib_curl, and the cf-bypass client (NOT -- cf-bypass uses raw httpx).

PD scrapers (gutendex, standard_ebooks, oapen_doab, wikisource) and the
async slow_servers probe in annas_curl still use httpx -- they do not benefit
from TLS fingerprint impersonation and their respx-based tests would break.
"""
from __future__ import annotations

import re
import threading
import time
from importlib.metadata import version as _pkg_version
from typing import Any
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as cffi_requests

from .anubis import solve_anubis


_ANUBIS_SIGNATURES = (
    re.compile(r'<meta\s+name="anubis-challenge"', re.I),
    re.compile(r'<title>[^<]*Making sure you[\'"]re not a bot', re.I),
    re.compile(r'name="generator"\s+content="anubis"', re.I),
)
_ANUBIS_CHALLENGE_RE = re.compile(r'<meta\s+name="anubis-challenge"\s+content="([^"]+)"', re.I)
_ANUBIS_DIFFICULTY_RE = re.compile(r'<meta\s+name="anubis-difficulty"\s+content="([0-9]+)"', re.I)
_ANUBIS_ACTION_RE = re.compile(r'<meta\s+name="anubis-action"\s+content="([^"]+)"', re.I)

_ANUBIS_COOKIE_CACHE: dict[str, tuple[str, float]] = {}  # host -> (jwt, expires_at)
_ANUBIS_TTL_SEC = 50 * 60
_ANUBIS_LOCK = threading.Lock()

# I-NEW-5: Depth guard — prevents infinite recursion if the Anubis submit
# endpoint itself returns an Anubis challenge page.
_ANUBIS_DEPTH_LIMIT = 2


# M15: project-default User-Agent for scrapers that do NOT impersonate
# a specific browser. Scrapers using make_client(impersonate="chrome")
# should NOT set this header — curl-cffi will set it to match Chrome.
try:
    _VERSION = _pkg_version("biblichor")
except Exception:
    from endless_library import __version__ as _VERSION
BIBLICHOR_USER_AGENT = f"biblichor/{_VERSION} (+https://github.com/SutanuNandigrami/biblichor)"


def make_client(
    *,
    impersonate: str = "chrome",
    timeout: float = 30.0,
    proxies: dict[str, str] | None = None,
) -> cffi_requests.Session:
    """Drop-in replacement for httpx.Client in scrapers.

    The returned Session has the same shape as httpx.Client for the
    methods we use: get/post/head/put/delete, returning a response
    with .status_code, .text, .content, .json(), .headers, .url.
    """
    s = cffi_requests.Session(impersonate=impersonate, timeout=timeout)
    if proxies:
        s.proxies.update(proxies)
    _install_anubis_middleware(s)
    return s


def _is_anubis_response(resp: Any) -> bool:
    if getattr(resp, "status_code", None) != 200:
        return False
    ctype = (resp.headers.get("content-type", "") if hasattr(resp, "headers") else "")
    if "text/html" not in ctype.lower():
        return False
    text = getattr(resp, "text", "") or ""
    return any(p.search(text) for p in _ANUBIS_SIGNATURES)


def _solve_and_get_cookie(
    html: str, request_url: str, *, raw_post: Any
) -> str | None:
    """Parse challenge + difficulty + action from HTML, solve the PoW,
    POST nonce+challenge using raw_post (the ORIGINAL session.post, not the
    wrapped version — prevents recursive Anubis interception on the submit
    endpoint itself, I-NEW-5).
    Returns the JWT cookie value or None.
    """
    cm = _ANUBIS_CHALLENGE_RE.search(html)
    dm = _ANUBIS_DIFFICULTY_RE.search(html)
    am = _ANUBIS_ACTION_RE.search(html)
    if not (cm and dm):
        return None
    challenge = cm.group(1)
    difficulty = int(dm.group(1))
    action = am.group(1) if am else "/.within.website/x/cmd/anubis/api/pass-challenge"
    nonce = solve_anubis(challenge, difficulty)
    submit_url = urljoin(request_url, action)
    # Use the raw (pre-wrap) post so we don't recurse into the wrapper
    resp = raw_post(submit_url,
                    data={"challenge": challenge, "nonce": str(nonce), "redir": request_url})
    if getattr(resp, "status_code", 0) not in (200, 302):
        return None
    for k, v in (getattr(resp, "cookies", {}) or {}).items():
        if "anubis" in k.lower() or k.lower().endswith("-auth"):
            return v
    return None


def _make_anubis_wrapper(session: Any, orig_fn: Any, raw_post_for_solve: Any):
    """Return a wrapped version of orig_fn that handles Anubis PoW challenges.

    Factored out so the same logic applies uniformly to get/post/head/put/delete
    (ultrareview I2). raw_post_for_solve is the ORIGINAL session.post captured
    before wrapping — used by _solve_and_get_cookie so the submit request does
    not recurse back through the Anubis wrapper (I-NEW-5).
    """
    _depth = [0]  # belt-and-suspenders recursion counter per wrapper instance

    def wrapper(url: str, **kw):
        host = urlparse(url).netloc
        # Read cached cookie outside the lock — dict.get is GIL-safe for reads
        cached = _ANUBIS_COOKIE_CACHE.get(host)
        if cached and cached[1] > time.time():
            kw.setdefault("cookies", {})
            kw["cookies"].setdefault("techaro-anubis-auth", cached[0])
        r = orig_fn(url, **kw)
        if _is_anubis_response(r) and _depth[0] < _ANUBIS_DEPTH_LIMIT:
            _depth[0] += 1
            try:
                cookie = _solve_and_get_cookie(r.text, url, raw_post=raw_post_for_solve)
            finally:
                _depth[0] -= 1
            if cookie:
                # Lock the write so concurrent threads do not tear the tuple
                with _ANUBIS_LOCK:
                    _ANUBIS_COOKIE_CACHE[host] = (cookie, time.time() + _ANUBIS_TTL_SEC)
                kw.setdefault("cookies", {})
                kw["cookies"]["techaro-anubis-auth"] = cookie
                r = orig_fn(url, **kw)
        return r
    return wrapper


def _install_anubis_middleware(session: Any) -> None:
    """Wrap get/post/head/put/delete so that on an Anubis-flavored response,
    we solve the PoW, store the cookie, and retry (ultrareview I2).

    I-NEW-5: capture raw session.post BEFORE wrapping so _solve_and_get_cookie
    uses the original post and does not recurse into the Anubis wrapper.
    """
    raw_post = session.post  # capture before wrapping
    for method in ("get", "post", "head", "put", "delete"):
        orig = getattr(session, method, None)
        if orig is None:
            continue
        setattr(session, method, _make_anubis_wrapper(session, orig, raw_post))
