"""Regression tests for the /healthz deploy_compose_writable guard.

Context: /app/deploy/compose.yml is the bind-mounted compose file the
biblichor container writes to when the operator runs an in-app
BookOrbit upgrade. Twice (memory: biblichor-bookorbit-cutover) the
host file lost its group-write bit and the upgrade flow started
returning 500 PermissionError silently — healthz stayed green.

The guard: if /app/deploy/compose.yml exists but isn't writable by the
healthz process (uid 1000 = biblichor inside the container), healthz
flips to 503 with deploy_compose_writable=False so the dashboard /
docker healthcheck / operator sees it immediately.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.db.schema import init_db
from endless_library.web import api as api_mod


def _build_app(*, db_path: Path) -> FastAPI:
    app = FastAPI()
    deps = SimpleNamespace(
        db_path=db_path,
        books=SimpleNamespace(pending=lambda **kw: []),
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


def _patch_compose_path(target: Path):
    """Patch the Path() constructor used inside healthz so the test
    can substitute /app/deploy/compose.yml for a tmp file."""
    real_path = Path
    return patch(
        "endless_library.web.api.Path",
        side_effect=lambda p=None, *a, **kw: target if str(p) == "/app/deploy/compose.yml" else real_path(p, *a, **kw),
    )


def test_healthz_passes_when_compose_yml_not_present(healthy_db: Path):
    """Most environments (pytest, dev) do not have /app/deploy/compose.yml.
    The guard must not trip in that case."""
    app = _build_app(db_path=healthy_db)
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200, r.json()
    body = r.json()
    # Field absent (because file doesn't exist) is the documented behavior.
    assert "deploy_compose_writable" not in body or body["deploy_compose_writable"] is True


def test_healthz_flips_to_503_when_compose_yml_not_writable(
    healthy_db: Path, tmp_path: Path
):
    """If /app/deploy/compose.yml exists but is read-only, healthz must
    return 503 with deploy_compose_writable=False."""
    compose = tmp_path / "compose.yml"
    compose.write_text("version: '3'\n")
    # Strip every write bit.
    os.chmod(compose, 0o444)
    try:
        app = _build_app(db_path=healthy_db)
        with _patch_compose_path(compose):
            r = TestClient(app).get("/healthz")
        assert r.status_code == 503, r.json()
        body = r.json()
        assert body["deploy_compose_writable"] is False
        assert body["ok"] is False
    finally:
        # Restore so pytest can clean up the tmp dir.
        os.chmod(compose, 0o644)


def test_healthz_passes_when_compose_yml_writable(
    healthy_db: Path, tmp_path: Path
):
    """Positive case: file exists and is group-writable -> healthz green."""
    compose = tmp_path / "compose.yml"
    compose.write_text("version: '3'\n")
    os.chmod(compose, 0o664)
    app = _build_app(db_path=healthy_db)
    with _patch_compose_path(compose):
        r = TestClient(app).get("/healthz")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["deploy_compose_writable"] is True
    assert body["ok"] is True
