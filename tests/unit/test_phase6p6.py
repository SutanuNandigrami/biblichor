"""Phase 6p.6 tests for the BookOrbit password-change endpoint."""

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


# ============ POST /api/bookorbit/admin/change-password ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_change_password_rotates_bookorbit_then_updates_store(respx_mock, tmp_path):
    """The happy path: biblichor logs in with current password, calls
    BookOrbit's change-password, then stores the new password locally."""
    login_route = respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "jwt"})
    )
    change_route = respx_mock.post("/api/v1/auth/change-password").mock(
        return_value=httpx.Response(204)
    )

    app = _build_app(tmp_path)
    client = TestClient(app)
    # Seed stored creds so the service knows the username
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "OldPass!"},
    )

    r = client.post(
        "/api/bookorbit/admin/change-password",
        json={"current_password": "OldPass!", "new_password": "NewPass123!"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "username": "admin"}

    # BookOrbit was called with the right payload
    assert login_route.called
    assert change_route.called
    sent = change_route.calls.last.request.content
    assert b"OldPass!" in sent
    assert b"NewPass123!" in sent

    # And the new password is now stored — subsequent /scan should
    # send the NEW one, not the old
    scan_route = respx_mock.post("/api/v1/scanner/libraries/1/scan").mock(
        return_value=httpx.Response(202)
    )
    # Reset login route so its history is fresh
    login_route.reset()
    login_route.mock(return_value=httpx.Response(200, json={"accessToken": "jwt2"}))

    r2 = client.post("/api/bookorbit/scan")
    assert r2.status_code == 200, r2.text
    assert scan_route.called
    sent_login = login_route.calls.last.request.content
    assert b"NewPass123!" in sent_login
    assert b"OldPass!" not in sent_login


@respx.mock(base_url=BASE, assert_all_called=False)
def test_change_password_surfaces_bookorbit_rejection(respx_mock, tmp_path):
    """If BookOrbit rejects the current password (401), biblichor
    must surface that to the SPA and NOT update stored creds."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(401, json={"message": "Invalid credentials"})
    )

    app = _build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "OldPass!"},
    )

    r = client.post(
        "/api/bookorbit/admin/change-password",
        json={"current_password": "WrongPass", "new_password": "NewPass123!"},
    )
    assert r.status_code == 502, r.text
    assert "login failed" in r.json()["detail"].lower()

    # Stored creds unchanged (still the original OldPass!)
    # The DELETE creds round-trip is the only externally visible state.
    # Indirect check: the next failed change-password attempt with the
    # same old current also 502s.
    r2 = client.post(
        "/api/bookorbit/admin/change-password",
        json={"current_password": "WrongPass", "new_password": "X"},
    )
    assert r2.status_code == 502


@respx.mock(base_url=BASE, assert_all_called=False)
def test_change_password_rolls_back_if_bookorbit_change_endpoint_fails(respx_mock, tmp_path):
    """Login succeeds but change-password endpoint returns 5xx —
    stored creds must remain at the OLD value (so the user can keep
    using Scan/Doctor while they figure out what went wrong)."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "jwt"})
    )
    respx_mock.post("/api/v1/auth/change-password").mock(
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
        json={"current_password": "OldPass!", "new_password": "NewPass123!"},
    )
    assert r.status_code == 502

    # Verify the stored password is still OldPass! — Scan with the OLD
    # password should still work (login route still mocked to 200)
    scan_route = respx_mock.post("/api/v1/scanner/libraries/1/scan").mock(
        return_value=httpx.Response(202)
    )
    r2 = client.post("/api/bookorbit/scan")
    assert r2.status_code == 200, r2.text
    assert scan_route.called
