"""Phase 6p.2 tests for the /api/bookorbit/* endpoints."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.config import BookOrbitCfg, Config, GeneralCfg
from endless_library.db.schema import init_db
from endless_library.web import api as api_mod

BASE = "http://bookorbit.test"


def _build_app(tmp_path: Path, *, bookorbit_enabled: bool = True, library_id: str = "") -> FastAPI:
    """Construct a FastAPI app with deps wired to a real on-disk db
    + a real recovery key file (so the service can derive its secrets
    key)."""
    db = tmp_path / "library.db"
    init_db(db)

    # Plant a fake age recovery key
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    rk = secrets_dir / "restore.key"
    rk.write_bytes(b"# public key: age1xyz\nAGE-SECRET-KEY-1ABC\n")

    # General.books_dir is what the service uses to FIND secrets_dir
    # (it does Path(books_dir).parent / "secrets"). So books_dir must
    # be tmp_path/data so .parent/secrets resolves to tmp_path/secrets.
    books_dir = tmp_path / "data"
    books_dir.mkdir(exist_ok=True)

    cfg = Config(
        general=GeneralCfg(books_dir=str(books_dir)),
        bookorbit=BookOrbitCfg(
            enabled=bookorbit_enabled,
            url=BASE,
            library_root=str(tmp_path / "library"),
            library_id=library_id,
        ),
    )
    (tmp_path / "library").mkdir(exist_ok=True)

    deps = SimpleNamespace(cfg=cfg, db_path=db, books=SimpleNamespace(pending=lambda **kw: []))

    app = FastAPI()
    app.state.deps = deps
    app.state.config_path = tmp_path / "config.yaml"
    (tmp_path / "config.yaml").write_text("general:\n  books_dir: " + str(books_dir) + "\n")
    app.state.scheduler = SimpleNamespace(running=True)
    api_mod.register(app)
    return app


# ============ GET /api/bookorbit/status ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_status_returns_full_payload(respx_mock, tmp_path):
    respx_mock.get("/api/v1/health").mock(
        return_value=httpx.Response(200, json={"info": {"database": {"status": "up"}}})
    )
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": False})
    )
    app = _build_app(tmp_path, library_id="lib-1")
    client = TestClient(app)
    r = client.get("/api/bookorbit/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["setup_needed"] is False
    assert body["has_creds"] is False  # not stored yet
    assert body["library_id"] == "lib-1"
    assert body["library_root_exists"] is True
    assert body["health_ok"] is True


@respx.mock(base_url=BASE, assert_all_called=False)
def test_status_surfaces_setup_needed_when_bookorbit_says_so(respx_mock, tmp_path):
    respx_mock.get("/api/v1/health").mock(
        return_value=httpx.Response(200, json={"info": {"database": {"status": "up"}}})
    )
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": True})
    )
    app = _build_app(tmp_path)
    body = TestClient(app).get("/api/bookorbit/status").json()
    assert body["setup_needed"] is True


def test_status_when_bookorbit_unreachable_surfaces_error(tmp_path):
    """If BookOrbit is down, status returns the error string in
    last_check_error rather than 500ing — the SPA needs to render
    a meaningful banner."""
    app = _build_app(tmp_path)
    body = TestClient(app).get("/api/bookorbit/status").json()
    assert body["health_ok"] is False
    assert body["last_check_error"] is not None


# ============ POST /api/bookorbit/setup ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_setup_creates_library_and_stores_creds(respx_mock, tmp_path):
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": True})
    )
    respx_mock.post("/api/v1/auth/setup").mock(return_value=httpx.Response(201, json={"id": "u1"}))
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "jwt"})
    )
    respx_mock.get("/api/v1/libraries").mock(return_value=httpx.Response(200, json=[]))
    respx_mock.post("/api/v1/libraries").mock(
        return_value=httpx.Response(201, json={"id": "lib-fresh"})
    )

    app = _build_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/bookorbit/setup",
        json={
            "admin_username": "admin",
            "admin_email": "a@x.com",
            "admin_name": "Admin",
            "admin_password": "Password1!",
            "setup_token": "tok",
            "library_root": str(tmp_path / "library"),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "library_id": "lib-fresh"}

    # Creds were stored
    status = client.get("/api/bookorbit/status").json()
    assert status["has_creds"] is True


def test_setup_rejected_when_bookorbit_disabled(tmp_path):
    app = _build_app(tmp_path, bookorbit_enabled=False)
    r = TestClient(app).post(
        "/api/bookorbit/setup",
        json={
            "admin_username": "a",
            "admin_email": "a@x.com",
            "admin_name": "A",
            "admin_password": "p",
            "setup_token": "t",
        },
    )
    assert r.status_code == 400
    assert "not enabled" in r.json()["detail"].lower()


# ============ POST /api/bookorbit/creds + DELETE ============


def test_creds_can_be_stored_and_cleared(tmp_path):
    app = _build_app(tmp_path)
    client = TestClient(app)
    r = client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "pw"},
    )
    assert r.status_code == 200
    # status should now say has_creds=True
    assert client.get("/api/bookorbit/status").json()["has_creds"] is True

    r2 = client.delete("/api/bookorbit/creds")
    assert r2.status_code == 200
    assert client.get("/api/bookorbit/status").json()["has_creds"] is False


# ============ POST /api/bookorbit/doctor ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_doctor_returns_check_list(respx_mock, tmp_path):
    respx_mock.get("/api/v1/health").mock(
        return_value=httpx.Response(200, json={"info": {"database": {"status": "up"}}})
    )
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": False})
    )
    respx_mock.get("/api/v1/opds").mock(return_value=httpx.Response(401))
    app = _build_app(tmp_path)
    body = TestClient(app).post("/api/bookorbit/doctor").json()
    assert "ok" in body
    assert isinstance(body["checks"], list)
    names = [c["name"] for c in body["checks"]]
    assert "health.reachable" in names
    assert "library_root.exists" in names


# ============ POST /api/bookorbit/scan ============


def test_scan_rejects_when_no_creds_stored(tmp_path):
    app = _build_app(tmp_path, library_id="lib-1")
    r = TestClient(app).post("/api/bookorbit/scan")
    assert r.status_code == 400
    assert "credentials" in r.json()["detail"].lower()


def test_scan_rejects_when_no_library_id(tmp_path):
    app = _build_app(tmp_path)  # library_id=""
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "pw"},
    )
    r = client.post("/api/bookorbit/scan")
    assert r.status_code == 400
    assert "library_id" in r.json()["detail"].lower()


@respx.mock(base_url=BASE, assert_all_called=False)
def test_scan_triggers_bookorbit_scanner(respx_mock, tmp_path):
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "jwt"})
    )
    scan_route = respx_mock.post("/api/v1/scanner/libraries/lib-1/scan").mock(
        return_value=httpx.Response(202)
    )
    app = _build_app(tmp_path, library_id="lib-1")
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "pw"},
    )
    r = client.post("/api/bookorbit/scan")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "library_id": "lib-1"}
    assert scan_route.called


# ============ POST /api/bookorbit/setup-token ============


def test_setup_token_returns_long_random_string(tmp_path):
    app = _build_app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/bookorbit/setup-token")
    assert r.status_code == 200
    tok = r.json()["token"]
    assert isinstance(tok, str)
    assert len(tok) >= 48
    # Two calls should give different tokens
    tok2 = client.post("/api/bookorbit/setup-token").json()["token"]
    assert tok != tok2
