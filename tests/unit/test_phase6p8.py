"""Phase 6p.8 tests: GUI-only password lifecycle.

Goal: the user never needs to type a current password, never needs
to read .env. biblichor authenticates internally via stored creds
or env-var fallback, then uses BookOrbit's admin reset flow."""

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
# JWT with payload {"sub": 1}, base64url-encoded, dot-separated
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


# ============ GUI flow: change password WITHOUT current_password ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_change_password_uses_stored_creds_when_current_not_supplied(respx_mock, tmp_path):
    """User opens the SPA card, types only new_password, hits Save.
    biblichor logs in with its stored creds, mints a reset URL,
    applies the new password, updates stored creds. The user never
    has to know or look up the current password."""
    respx_mock.post("/api/v1/auth/login").mock(
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
    # bootstrap-equivalent: creds already in encrypted store
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "BootstrapPassword!"},
    )

    # The user types ONLY new_password
    r = client.post(
        "/api/bookorbit/admin/change-password",
        json={"new_password": "NewMemorablePassword1!"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "username": "admin"}
    assert reset_route.called

    # The reset endpoint was called with the new password
    reset_body = reset_route.calls.last.request.content
    assert b"NewMemorablePassword1!" in reset_body
    assert b"RTK" in reset_body  # the reset token from mint_reset_url

    # And the stored creds were rotated — Scan now uses the NEW password
    respx_mock.post("/api/v1/scanner/libraries/1/scan").mock(return_value=httpx.Response(202))
    login_route = respx_mock.post("/api/v1/auth/login")
    login_route.reset()
    login_route.mock(return_value=httpx.Response(200, json={"accessToken": JWT}))

    r2 = client.post("/api/bookorbit/scan")
    assert r2.status_code == 200, r2.text
    sent_login = login_route.calls.last.request.content
    assert b"NewMemorablePassword1!" in sent_login
    assert b"BootstrapPassword!" not in sent_login


# ============ Env-var fallback ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_change_password_falls_back_to_env_when_no_stored_creds(respx_mock, monkeypatch, tmp_path):
    """First container start, no creds in db yet, but compose passes
    BOOKORBIT_ADMIN_PASSWORD in env. The SPA Change-password card
    must still work."""
    monkeypatch.setenv("BOOKORBIT_ADMIN_PASSWORD", "EnvBootstrapPw!")
    monkeypatch.setenv("BOOKORBIT_ADMIN_USER", "admin")

    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": JWT})
    )
    respx_mock.post("/api/v1/users/1/reset-password").mock(
        return_value=httpx.Response(
            200, json={"resetUrl": "http://bookorbit/reset-password?token=RTK"}
        )
    )
    respx_mock.post("/api/v1/auth/reset-password").mock(return_value=httpx.Response(204))

    app = _build_app(tmp_path)
    client = TestClient(app)
    # NOTE: no /api/bookorbit/creds call — relies on env fallback

    r = client.post(
        "/api/bookorbit/admin/change-password",
        json={"new_password": "UserChoicePassword1!"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "username": "admin"}

    # And the stored creds now hold the new password
    status = client.get("/api/bookorbit/status").json()
    assert status["has_creds"] is True


# ============ Sane error when both stored and env are unavailable ============


def test_change_password_clear_error_when_no_credentials_available(monkeypatch, tmp_path):
    monkeypatch.delenv("BOOKORBIT_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("BOOKORBIT_ADMIN_USER", raising=False)
    app = _build_app(tmp_path)
    r = TestClient(app).post(
        "/api/bookorbit/admin/change-password",
        json={"new_password": "X12345678"},
    )
    # 400 (clean refusal) — not 502 (downstream error)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"].lower()
    assert "credentials" in detail
    assert "stored creds" in detail or "compose env" in detail


# ============ The user's reported flow: current_password optional in payload ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_payload_without_current_password_field_is_accepted(respx_mock, tmp_path):
    """The /api/bookorbit/admin/change-password endpoint must accept
    a payload that omits current_password entirely (the GUI default)."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": JWT})
    )
    respx_mock.post("/api/v1/users/1/reset-password").mock(
        return_value=httpx.Response(
            200, json={"resetUrl": "http://bookorbit/reset-password?token=T"}
        )
    )
    respx_mock.post("/api/v1/auth/reset-password").mock(return_value=httpx.Response(204))

    app = _build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "x"},
    )
    r = client.post(
        "/api/bookorbit/admin/change-password",
        json={"new_password": "NewPwOnly!"},
    )
    # Specifically: NOT 422 (validation error for missing current_password)
    assert r.status_code != 422
    assert r.status_code == 200, r.text


# ============ resolve_current_credentials priority: stored > env ============


def test_stored_creds_take_priority_over_env(monkeypatch, tmp_path):
    """When both stored and env are present, stored wins (more
    recent in the user's mental model)."""
    monkeypatch.setenv("BOOKORBIT_ADMIN_PASSWORD", "OldEnvPw")
    monkeypatch.setenv("BOOKORBIT_ADMIN_USER", "admin")

    app = _build_app(tmp_path)
    client = TestClient(app)
    client.post(
        "/api/bookorbit/creds",
        json={"admin_username": "admin", "admin_password": "StoredPw"},
    )

    # Direct service-level check: resolve returns stored
    from endless_library.bookorbit.service import BookOrbitService

    deps = app.state.deps
    svc = BookOrbitService(
        cfg=deps.cfg,
        db_path=deps.db_path,
        restore_key_path=Path(deps.cfg.general.books_dir).parent / "secrets" / "restore.key",
    )
    creds = svc._resolve_current_credentials()
    assert creds == ("admin", "StoredPw")
