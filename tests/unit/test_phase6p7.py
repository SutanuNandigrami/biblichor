"""Phase 6p.7 tests for the tightened setup + recreate-library flow."""

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


def _build_app(tmp_path: Path) -> FastAPI:
    db = tmp_path / "library.db"
    init_db(db)
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "restore.key").write_bytes(b"# public key: age1xyz\nAGE-SECRET-KEY-1ABC\n")
    books_dir = tmp_path / "data"
    books_dir.mkdir(exist_ok=True)

    cfg = Config(
        general=GeneralCfg(books_dir=str(books_dir)),
        bookorbit=BookOrbitCfg(
            enabled=True,
            url=BASE,
            library_root=str(tmp_path / "library"),
            library_id="",
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


# ============ run_setup refuses cleanly when already set up ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_setup_refuses_when_bookorbit_already_set_up(respx_mock, tmp_path):
    """The user's reported bug: clicking the setup wizard with
    admin/admin against a BookOrbit that's already bootstrapped used
    to fail with an opaque '401 Invalid credentials'. Now it must
    return a 400 with a clear directive."""
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": False})
    )

    app = _build_app(tmp_path)
    r = TestClient(app).post(
        "/api/bookorbit/setup",
        json={
            "admin_username": "admin",
            "admin_email": "a@x.com",
            "admin_name": "Admin",
            "admin_password": "admin",
            "setup_token": "t",
        },
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "already set up" in detail.lower()
    # Tell the user where to go instead
    assert "change password" in detail.lower()
    assert "stored creds" in detail.lower()
    # Mention the recovery path
    assert "recreate" in detail.lower()


# ============ recreate_watched_library happy path ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_recreate_library_finds_existing_library(respx_mock, tmp_path):
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "jwt"})
    )
    respx_mock.get("/api/v1/libraries").mock(
        return_value=httpx.Response(200, json=[{"id": "lib-existing", "name": "biblichor"}])
    )

    app = _build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "pw"},
    )
    r = client.post("/api/bookorbit/recreate-library")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "library_id": "lib-existing", "created": False}


@respx.mock(base_url=BASE, assert_all_called=False)
def test_recreate_library_creates_when_missing(respx_mock, tmp_path):
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "jwt"})
    )
    respx_mock.get("/api/v1/libraries").mock(return_value=httpx.Response(200, json=[]))
    respx_mock.post("/api/v1/libraries").mock(
        return_value=httpx.Response(201, json={"id": "lib-new"})
    )

    app = _build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "pw"},
    )
    r = client.post("/api/bookorbit/recreate-library")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "library_id": "lib-new", "created": True}


def test_recreate_library_refuses_without_stored_creds(tmp_path):
    app = _build_app(tmp_path)
    r = TestClient(app).post("/api/bookorbit/recreate-library")
    assert r.status_code == 400
    assert "stored credentials" in r.json()["detail"].lower()
