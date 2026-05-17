"""FlareSolverr client session lifecycle."""

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
    def __init__(self, queue):
        self.queue = list(queue)
        self.posted = []

    def post(self, url, json):
        self.posted.append((url, json))
        return self.queue.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_create_and_destroy_session():
    client = _Client(
        [
            _Resp({"status": "ok"}),  # sessions.create
            _Resp({"status": "ok"}),  # sessions.destroy
        ]
    )
    fs = FlareSolverr("http://x:8191/v1", client_factory=lambda: client)
    with fs.session() as sid:
        assert sid.startswith("el-")
    posted_cmds = [p[1]["cmd"] for p in client.posted]
    assert posted_cmds == ["sessions.create", "sessions.destroy"]


def test_get_includes_session_id_when_provided():
    client = _Client(
        [
            _Resp(
                {
                    "status": "ok",
                    "solution": {"status": 200, "response": "hi", "userAgent": "", "cookies": []},
                }
            )
        ]
    )
    fs = FlareSolverr("http://x:8191/v1", client_factory=lambda: client)
    fs.get("https://annas-archive.gl/", session="my-sid")
    assert client.posted[0][1]["session"] == "my-sid"


def test_get_without_session_param():
    client = _Client(
        [
            _Resp(
                {
                    "status": "ok",
                    "solution": {"status": 200, "response": "hi", "userAgent": "", "cookies": []},
                }
            )
        ]
    )
    fs = FlareSolverr("http://x:8191/v1", client_factory=lambda: client)
    fs.get("https://annas-archive.gl/")
    assert "session" not in client.posted[0][1]


def test_create_session_failure_raises():
    client = _Client([_Resp({"status": "error", "message": "boom"})])
    fs = FlareSolverr("http://x:8191/v1", client_factory=lambda: client)
    with pytest.raises(FlareSolverrError):
        fs.create_session()
