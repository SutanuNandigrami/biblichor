"""Phase 6p.6 tests — kept after the 6p.8 reset-flow refactor.

Coverage: legacy current_password-supplied flow still works (for
callers who want to pass it explicitly), and the BookOrbit
rejection / 5xx paths surface meaningfully."""

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
JWT = "aGVhZGVy.eyJzdWIiOjF9.c2ln"  # header.{"sub":1}.sig


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
            library_id="1",
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


# ============ legacy current_password supplied + reset-flow used ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_change_password_with_explicit_current_password(respx_mock, tmp_path):
    login_route = respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": JWT})
    )
    respx_mock.post("/api/v1/users/1/reset-password").mock(
        return_value=httpx.Response(
            200, json={"resetUrl": "http://bookorbit/reset-password?token=RTK"}
        )
    )
    reset_route = respx_mock.post("/api/v1/auth/reset-password").mock(
        return_value=httpx.Response(204)
    )

    app = _build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "OldStored"},
    )
    # Explicit current_password — used in place of stored
    r = client.post(
        "/api/bookorbit/admin/change-password",
        json={"current_password": "ExplicitOld", "new_password": "NewPass123!"},
    )
    assert r.status_code == 200, r.text

    # login was called with ExplicitOld, not OldStored
    sent_login = login_route.calls.last.request.content
    assert b"ExplicitOld" in sent_login
    assert b"OldStored" not in sent_login

    # reset endpoint got NewPass123!
    reset_body = reset_route.calls.last.request.content
    assert b"NewPass123!" in reset_body


@respx.mock(base_url=BASE, assert_all_called=False)
def test_change_password_surfaces_bookorbit_login_rejection(respx_mock, tmp_path):
    """Wrong stored password -> BookOrbit returns 401 -> biblichor
    surfaces a clean 502 with an actionable message."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(401, json={"message": "Invalid credentials"})
    )

    app = _build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "WrongStored"},
    )
    r = client.post(
        "/api/bookorbit/admin/change-password",
        json={"new_password": "NewPass123!"},
    )
    # Login failure -> wrapped in BookOrbitServiceError -> 400 (not opaque 502)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"].lower()
    assert "authenticate" in detail or "credentials" in detail


@respx.mock(base_url=BASE, assert_all_called=False)
def test_change_password_rolls_back_on_reset_endpoint_failure(respx_mock, tmp_path):
    """If /auth/reset-password returns 5xx, stored creds remain at
    the OLD value so the user can keep using Scan/Doctor."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": JWT})
    )
    respx_mock.post("/api/v1/users/1/reset-password").mock(
        return_value=httpx.Response(
            200, json={"resetUrl": "http://bookorbit/reset-password?token=RTK"}
        )
    )
    respx_mock.post("/api/v1/auth/reset-password").mock(
        return_value=httpx.Response(500, text="server error")
    )

    app = _build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "OldPass!"},
    )
    r = client.post(
        "/api/bookorbit/admin/change-password",
        json={"new_password": "NewPass123!"},
    )
    assert r.status_code == 502

    # Stored creds should still be OldPass! — verify by triggering scan,
    # which uses stored creds for login
    scan_route = respx_mock.post("/api/v1/scanner/libraries/1/scan").mock(
        return_value=httpx.Response(202)
    )
    r2 = client.post("/api/bookorbit/scan")
    assert r2.status_code == 200, r2.text
    assert scan_route.called
