"""Regression + feature-intact tests for /healthz.

The previous /api/healthz returned 200 unconditionally and only
included queue_size + db_size_bytes. bootstrap.sh and Docker
healthcheck need a probe that:

  - is at the root path (/healthz, not /api/...)
  - returns 503 when any component is broken
  - reports DB / scraper-registry / scheduler status explicitly

Regression tests verify each failure mode flips the response to 503.
Feature-intact tests verify the happy path still returns 200 and
contains the documented fields.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.db.schema import connect, init_db
from endless_library.web import api as api_mod


def _build_app(*, db_path: Path, scheduler_running: bool = True) -> FastAPI:
    """Construct a minimal FastAPI app with just enough deps to make
    /healthz answer correctly."""
    app = FastAPI()
    deps = SimpleNamespace(
        db_path=db_path,
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.deps = deps
    app.state.scheduler = SimpleNamespace(running=scheduler_running)
    api_mod.register(app)
    return app


@pytest.fixture
def healthy_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_db(db)
    return db


# ============ FEATURE-INTACT: happy path ============


def test_healthz_returns_200_when_all_healthy(healthy_db: Path):
    app = _build_app(db_path=healthy_db, scheduler_running=True)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["db"] is True
    assert isinstance(body["scrapers"], int) and body["scrapers"] > 0
    assert body["scheduler"] is True
    assert "queue_size" in body
    assert "db_size_bytes" in body


def test_healthz_is_at_root_not_under_api(healthy_db: Path):
    """Bootstrap.sh and Docker healthcheck probe /healthz at root.
    A regression that moves it back under /api breaks those callers."""
    app = _build_app(db_path=healthy_db)
    client = TestClient(app)
    r_root = client.get("/healthz")
    r_api = client.get("/api/healthz")
    # Root must exist
    assert r_root.status_code in (200, 503)
    # /api/healthz must NOT exist (we moved it out)
    assert r_api.status_code == 404


# ============ REGRESSION: each component failure flips to 503 ============


def test_healthz_returns_503_when_db_unreachable(tmp_path: Path):
    """DB ping fails -> 503 with component-level detail."""
    # Point at a path inside a non-existent dir; connect() will fail
    bogus_db = tmp_path / "nope" / "deeper" / "missing.db"
    app = _build_app(db_path=bogus_db, scheduler_running=True)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert isinstance(body["db"], str)
    assert body["db"].startswith("down:")


def test_healthz_returns_503_when_scheduler_stopped(healthy_db: Path):
    """Scheduler.running=False -> 503. We want bootstrap.sh to wait
    until the scheduler has actually started, not just the HTTP server."""
    app = _build_app(db_path=healthy_db, scheduler_running=False)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["scheduler"] is False


def test_healthz_returns_503_when_scheduler_missing(healthy_db: Path):
    """During cold startup, app.state.scheduler may not yet be assigned.
    Treat as not-yet-ready (503)."""
    app = FastAPI()
    deps = SimpleNamespace(
        db_path=healthy_db,
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.deps = deps
    # NOTE: no scheduler stashed
    api_mod.register(app)

    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body["scheduler"] is False


# ============ REGRESSION: response body shape ============


def test_healthz_body_always_contains_documented_fields(healthy_db: Path):
    """The contract the dashboard and bootstrap.sh will rely on:
    these keys MUST be present in every response (200 or 503)."""
    REQUIRED_KEYS = {"ok", "db", "scrapers", "scheduler", "queue_size", "db_size_bytes"}

    # Happy path
    app = _build_app(db_path=healthy_db, scheduler_running=True)
    body = TestClient(app).get("/healthz").json()
    assert REQUIRED_KEYS <= set(body.keys()), f"missing keys: {REQUIRED_KEYS - set(body.keys())}"

    # Unhealthy path
    app_bad = _build_app(db_path=healthy_db, scheduler_running=False)
    body_bad = TestClient(app_bad).get("/healthz").json()
    assert REQUIRED_KEYS <= set(body_bad.keys()), f"missing keys: {REQUIRED_KEYS - set(body_bad.keys())}"


def test_healthz_scrapers_count_matches_registry(healthy_db: Path):
    """Documents that the scraper count is sourced from registry.available()
    so the dashboard and external monitors can detect when a scraper
    fails to register."""
    from endless_library.scrapers import registry as r

    expected = len(r.available())
    app = _build_app(db_path=healthy_db, scheduler_running=True)
    body = TestClient(app).get("/healthz").json()
    assert body["scrapers"] == expected
