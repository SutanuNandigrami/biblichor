"""Regression tests for /healthz BookOrbit reachability probe.

Context: 2026-05-25 outage — `deploy/.env` symlink was missing, so
docker compose substituted empty strings for BookOrbit's
POSTGRES_PASSWORD / JWT_SECRET, and BookOrbit crashloop-restarted for
~30 minutes. biblichor's /healthz stayed green throughout because no
component probed BookOrbit's HTTP API. Fix: probe BO's /api/v1/health
when BO is configured-enabled, flip ok=False on failure.

Cases pinned:
1. BO disabled in config -> body.bookorbit.reachable == None,
   ok stays True (BO is optional).
2. BO enabled + healthy -> reachable=True, ok=True.
3. BO enabled + unreachable -> reachable=False, ok=False -> 503.
4. BO enabled + slow (timeout) -> reachable=False, ok=False -> 503.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.db.schema import init_db
from endless_library.web import api as api_mod


def _build_app(
    *, db_path: Path, bo_enabled: bool, bo_url: str = "http://bookorbit:3000"
) -> FastAPI:
    app = FastAPI()
    deps = SimpleNamespace(
        db_path=db_path,
        books=SimpleNamespace(pending=lambda **kw: []),
        cfg=SimpleNamespace(
            bookorbit=SimpleNamespace(enabled=bo_enabled, url=bo_url),
        ),
    )
    app.state.deps = deps
    app.state.scheduler = SimpleNamespace(running=True)
    api_mod.register(app)
    return app


@pytest.fixture
def healthy_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_db(db)
    return db


def test_bo_disabled_does_not_flip_healthz(healthy_db: Path):
    """When BookOrbit is not enabled, healthz must NOT probe it and must
    NOT flip to 503. The bookorbit field reports reachable=None with a
    'not configured' reason so the dashboard can render the unconfigured
    state cleanly."""
    app = _build_app(db_path=healthy_db, bo_enabled=False)
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bookorbit"]["reachable"] is None
    assert "not configured" in body["bookorbit"].get("reason", "")


def test_bo_enabled_and_healthy_keeps_ok_true(healthy_db: Path):
    """BookOrbit enabled + responds 200 -> reachable=True, ok=True."""
    app = _build_app(db_path=healthy_db, bo_enabled=True)

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    with patch("urllib.request.urlopen", return_value=_Resp()):
        r = TestClient(app).get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bookorbit"]["reachable"] is True
    assert body["bookorbit"]["url"].endswith("/api/v1/health")


def test_bo_enabled_but_unreachable_flips_to_503(healthy_db: Path):
    """BookOrbit enabled + connection refused -> reachable=False,
    ok=False, HTTP 503. This is the crashloop case from 2026-05-25."""
    app = _build_app(db_path=healthy_db, bo_enabled=True)

    with patch("urllib.request.urlopen", side_effect=URLError("Connection refused")):
        r = TestClient(app).get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["bookorbit"]["reachable"] is False
    assert (
        "URLError" in body["bookorbit"]["error"]
        or "Connection refused" in body["bookorbit"]["error"]
    )


def test_bo_enabled_but_timeout_flips_to_503(healthy_db: Path):
    """BookOrbit reachable but stalled past the 2s probe timeout -> 503.
    Prevents a hung BO from being silently mis-reported as 'healthy'."""
    app = _build_app(db_path=healthy_db, bo_enabled=True)

    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        r = TestClient(app).get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["bookorbit"]["reachable"] is False
