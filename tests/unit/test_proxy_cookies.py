"""Regression + feature-intact tests for the Calibre-Web proxy
Set-Cookie collision fix.

Audit finding: proxy_calibre built the Response with `headers=dict(
out_headers)`, which collapses duplicate keys. Calibre-Web sets
multiple cookies in a single response (e.g. session + remember_token
on login) — all but one were silently lost.

Regression test: simulate upstream returning two Set-Cookie headers,
assert both survive on the proxy response.
Feature-intact: other behaviors (path rewriting, Location, HTML
rewriting, non-cookie headers) still work.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.web import proxy_calibre


def _build_app() -> FastAPI:
    app = FastAPI()
    proxy_calibre.register(app)
    return app


def _fake_upstream_response(
    *,
    status: int = 200,
    headers: list[tuple[str, str]],
    body: bytes = b"",
) -> httpx.Response:
    """Build an httpx.Response that mimics what Calibre-Web would return.

    Use a Request to satisfy httpx's constructor."""
    req = httpx.Request("GET", "http://127.0.0.1:8083/")
    resp = httpx.Response(
        status_code=status,
        headers=httpx.Headers(headers),
        content=body,
        request=req,
    )
    return resp


# ============ REGRESSION ============


def test_duplicate_set_cookie_preserved(monkeypatch):
    """Calibre-Web setting two cookies in one response must NOT be
    collapsed by the proxy. Before the fix, only the last one survived."""
    upstream = _fake_upstream_response(
        headers=[
            ("content-type", "text/html"),
            ("set-cookie", "session=abc123; Path=/; HttpOnly"),
            ("set-cookie", "remember_token=xyz; Path=/admin; HttpOnly"),
        ],
        body=b"<html></html>",
    )

    async def fake_request(self, method, url, **kw):
        return upstream

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    client = TestClient(_build_app())
    r = client.get("/library/")
    # Starlette's TestClient surfaces multiple Set-Cookie via the raw
    # headers list.
    set_cookies = [v for k, v in r.headers.raw if k.lower() == b"set-cookie"]
    assert len(set_cookies) == 2, f"got {len(set_cookies)} Set-Cookie, expected 2: {set_cookies}"
    # Both values are present (Path is rewritten — that's part of the proxy)
    joined = b"\n".join(set_cookies).decode()
    assert "session=abc123" in joined
    assert "remember_token=xyz" in joined


def test_set_cookie_path_rewritten_for_all_duplicates(monkeypatch):
    """Each surviving cookie must also get its Path rewritten to
    /library/<...> so the browser sends them back on proxy requests."""
    upstream = _fake_upstream_response(
        headers=[
            ("content-type", "text/html"),
            ("set-cookie", "session=a; Path=/; HttpOnly"),
            ("set-cookie", "csrf=b; Path=/api"),
        ],
        body=b"<html></html>",
    )

    async def fake_request(self, method, url, **kw):
        return upstream

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    client = TestClient(_build_app())
    r = client.get("/library/")
    set_cookies = [v.decode() for k, v in r.headers.raw if k.lower() == b"set-cookie"]
    # Both rewrites should land at /library/...
    assert any("Path=/library/" in c for c in set_cookies)
    # Original /api re-prefixed to /library/api
    assert any("Path=/library/api" in c for c in set_cookies)


# ============ FEATURE-INTACT ============


def test_single_set_cookie_still_works(monkeypatch):
    """Don't regress the single-cookie case."""
    upstream = _fake_upstream_response(
        headers=[
            ("content-type", "text/html"),
            ("set-cookie", "session=solo; Path=/; HttpOnly"),
        ],
        body=b"<html></html>",
    )

    async def fake_request(self, method, url, **kw):
        return upstream

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = TestClient(_build_app())
    r = client.get("/library/")
    set_cookies = [v.decode() for k, v in r.headers.raw if k.lower() == b"set-cookie"]
    assert len(set_cookies) == 1
    assert "session=solo" in set_cookies[0]
    assert "Path=/library/" in set_cookies[0]


def test_location_header_rewritten(monkeypatch):
    """Location: /login → /library/login still works (not regressed by
    the Response refactor)."""
    upstream = _fake_upstream_response(
        status=302,
        headers=[("location", "/login?next=/admin")],
    )

    async def fake_request(self, method, url, **kw):
        return upstream

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = TestClient(_build_app(), follow_redirects=False)
    r = client.get("/library/")
    assert r.status_code == 302
    # Location: /login → /library/login
    assert r.headers["location"] == "/library/login?next=/admin"


def test_html_body_path_rewrite_still_works(monkeypatch):
    """The Response refactor shouldn't have affected body rewriting."""
    upstream = _fake_upstream_response(
        headers=[("content-type", "text/html")],
        body=b'<link href="/static/style.css"><a href="/admin">x</a>',
    )

    async def fake_request(self, method, url, **kw):
        return upstream

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = TestClient(_build_app())
    r = client.get("/library/")
    assert b"/library/static/style.css" in r.content
    assert b"/library/admin" in r.content


def test_non_cookie_headers_passthrough(monkeypatch):
    """Cache-Control, custom headers, etc must still come through."""
    upstream = _fake_upstream_response(
        headers=[
            ("content-type", "application/json"),
            ("cache-control", "no-cache"),
            ("x-calibre-version", "0.6.x"),
        ],
        body=b'{"ok": true}',
    )

    async def fake_request(self, method, url, **kw):
        return upstream

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = TestClient(_build_app())
    r = client.get("/library/api/whatever")
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("x-calibre-version") == "0.6.x"


def test_hop_by_hop_headers_stripped(monkeypatch):
    """Hop-by-hop headers (transfer-encoding, connection) must NOT be
    forwarded — RFC 7230. This was working before, should still work."""
    upstream = _fake_upstream_response(
        headers=[
            ("content-type", "text/html"),
            ("transfer-encoding", "chunked"),
            ("connection", "close"),
            ("upgrade", "websocket"),
        ],
        body=b"<html></html>",
    )

    async def fake_request(self, method, url, **kw):
        return upstream

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = TestClient(_build_app())
    r = client.get("/library/")
    assert "transfer-encoding" not in {k.lower() for k in r.headers}
    assert "connection" not in {k.lower() for k in r.headers}
    assert "upgrade" not in {k.lower() for k in r.headers}
