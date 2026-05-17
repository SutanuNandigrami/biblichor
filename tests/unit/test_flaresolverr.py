from __future__ import annotations

import pytest

from endless_library.flaresolverr import FlareSolverr, FlareSolverrError


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class _Client:
    def __init__(self, resp: _Resp):
        self.resp = resp
        self.posted = []

    def post(self, url, json):
        self.posted.append((url, json))
        return self.resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _factory(resp):
    return lambda: _Client(resp)


def test_get_success():
    resp = _Resp(
        {
            "status": "ok",
            "solution": {
                "status": 200,
                "response": "<html>hello</html>",
                "userAgent": "Mozilla/5.0 Chrome/142",
                "cookies": [{"name": "cf_clearance", "value": "abc"}],
            },
        }
    )
    fs = FlareSolverr("http://flaresolverr:8191/v1", client_factory=_factory(resp))
    r = fs.get("https://annas-archive.gl/search?q=x")
    assert r.status_code == 200
    assert "hello" in r.text
    assert r.user_agent.startswith("Mozilla")
    assert r.cookies[0]["name"] == "cf_clearance"


def test_get_raises_on_non_ok():
    resp = _Resp({"status": "error", "message": "challenge solver failed"})
    fs = FlareSolverr("http://x:8191/v1", client_factory=_factory(resp))
    with pytest.raises(FlareSolverrError):
        fs.get("https://annas-archive.gl/")


def test_payload_includes_cmd_url_timeout():
    resp = _Resp({"status": "ok", "solution": {"status": 200, "response": ""}})
    fs = FlareSolverr("http://x:8191/v1", max_timeout_ms=45_000, client_factory=_factory(resp))
    fs.get("https://annas-archive.gl/search")
    # First call landed in _client.posted via _factory; we can't reach it directly
    # because _factory() makes a new _Client each call. Re-verify by re-invoking
    # with an inspector factory.
    seen = {}

    def inspect():
        c = _Client(resp)
        seen["client"] = c
        return c

    fs2 = FlareSolverr("http://x:8191/v1", max_timeout_ms=45_000, client_factory=inspect)
    fs2.get("https://annas-archive.gl/search")
    url, payload = seen["client"].posted[0]
    assert url.endswith("/v1")
    assert payload["cmd"] == "request.get"
    assert payload["url"] == "https://annas-archive.gl/search"
    assert payload["maxTimeout"] == 45_000
