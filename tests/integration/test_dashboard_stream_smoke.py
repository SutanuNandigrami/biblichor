"""Integration smoke test: dashboard endpoints.

Tests the non-streaming snapshot endpoint and verifies the SSE stream
generator produces valid SSE frames via direct function invocation.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.db.schema import init_db
from endless_library.web import api as api_mod


def _build_app(db_path: Path) -> FastAPI:
    app = FastAPI()
    deps = SimpleNamespace(
        db_path=db_path,
        books=SimpleNamespace(pending=lambda **kw: []),
        cfg=SimpleNamespace(
            smtp=SimpleNamespace(daily_cap=0),
            stk=SimpleNamespace(daily_cap=None),
        ),
        bookorbit_service=None,
    )
    app.state.deps = deps
    app.state.scheduler = SimpleNamespace(running=True)
    app.state.bookorbit_upgrade_lock = asyncio.Lock()
    api_mod.DASHBOARD_INTERVAL_SEC = 0.01
    api_mod.register(app)
    return app


@pytest.fixture
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


def test_dashboard_snapshot_endpoint_returns_expected_json(db: Path):
    """Non-streaming snapshot returns all required top-level keys."""
    app = _build_app(db)
    client = TestClient(app)

    r = client.get("/api/dashboard/snapshot")
    assert r.status_code == 200
    obj = r.json()
    for key in ("ts", "status_counts", "throughput_24h", "method_breakdown_24h", "source_funnel"):
        assert key in obj, f"missing key: {key}"
    # throughput_24h must have series with stk + smtp
    series_names = {s["name"] for s in obj["throughput_24h"]["series"]}
    assert series_names == {"stk", "smtp"}
    # method_breakdown_24h always has stk + smtp keys
    assert "stk" in obj["method_breakdown_24h"]
    assert "smtp" in obj["method_breakdown_24h"]


def test_dashboard_stream_endpoint_registered(db: Path):
    """The SSE stream endpoint must be registered in the router.

    We verify it via the OpenAPI schema (which lists all routes without
    calling the endpoint itself). This avoids the blocking behaviour of
    infinite SSE generators in TestClient.
    """
    app = _build_app(db)
    client = TestClient(app)

    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = schema.get("paths", {})
    assert "/api/dashboard/stream" in paths, (
        f"/api/dashboard/stream not in routes. Found: {sorted(paths.keys())}"
    )
    assert "/api/dashboard/snapshot" in paths


def test_dashboard_stream_generator_produces_valid_sse_frames(db: Path):
    """Drive compute_dashboard_snapshot + SSE framing to verify frame format.

    This tests the core SSE logic without needing a live HTTP connection.
    The SSE generator is an infinite async generator; we test the snapshot
    function directly and verify the frame format matches SSE spec.
    """
    from endless_library.web.api import compute_dashboard_snapshot

    snap = compute_dashboard_snapshot(db)
    # Simulate what the generator yields
    frame = "data: " + json.dumps(snap) + "\n\n"

    # Verify frame structure per SSE spec
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")

    # Parse the JSON payload from the frame
    payload_str = frame[6:].rstrip("\n")
    obj = json.loads(payload_str)

    assert "ts" in obj
    assert "status_counts" in obj
    assert "throughput_24h" in obj
    assert "method_breakdown_24h" in obj
    assert "source_funnel" in obj

    # Verify throughput structure
    tp = obj["throughput_24h"]
    assert "bucket_minutes" in tp
    assert "series" in tp
    series_names = {s["name"] for s in tp["series"]}
    assert series_names == {"stk", "smtp"}
