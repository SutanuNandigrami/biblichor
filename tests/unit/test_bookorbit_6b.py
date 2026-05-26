"""Regression + feature-intact tests for the BookOrbit client + setup
helper (Phase 6b). httpx is mocked via respx so we don't need a live
BookOrbit instance to validate the request shapes.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from endless_library.bookorbit.client import (
    BookOrbitClient,
    BookOrbitError,
)
from endless_library.bookorbit.setup import ensure_bookorbit_ready

BASE = "http://bookorbit.test"


# ============ Client auth flows ============


@respx.mock(base_url=BASE)
def test_setup_status_returns_json(respx_mock):
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": True})
    )
    with BookOrbitClient(BASE) as c:
        assert c.setup_status() == {"needsSetup": True}


@respx.mock(base_url=BASE)
def test_setup_admin_sends_correct_header_and_body(respx_mock):
    route = respx_mock.post("/api/v1/auth/setup").mock(
        return_value=httpx.Response(201, json={"id": "u1"})
    )
    with BookOrbitClient(BASE) as c:
        c.setup_admin(
            token="t0k3n",
            username="admin",
            name="Admin",
            email="admin@x.com",
            password="Password123",
        )
    assert route.called
    sent = route.calls.last.request
    assert sent.headers.get("x-setup-token") == "t0k3n"
    body = json.loads(sent.content)
    assert body == {
        "username": "admin",
        "name": "Admin",
        "email": "admin@x.com",
        "password": "Password123",
    }


@respx.mock(base_url=BASE)
def test_setup_admin_raises_clear_error_on_failure(respx_mock):
    respx_mock.post("/api/v1/auth/setup").mock(
        return_value=httpx.Response(403, text="setup token mismatch")
    )
    with BookOrbitClient(BASE) as c, pytest.raises(BookOrbitError, match="setup failed"):
        c.setup_admin(token="bad", username="x", name="x", email="x@x.com", password="Password1")


@respx.mock(base_url=BASE)
def test_login_stashes_jwt(respx_mock):
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "jwt-abc"})
    )
    with BookOrbitClient(BASE) as c:
        c.login(username="admin", password="Password1")
        assert c._jwt == "jwt-abc"


@respx.mock(base_url=BASE)
def test_login_supports_snake_case_response(respx_mock):
    """Some Nest serializers emit access_token; we accept either."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "jwt-snake"})
    )
    with BookOrbitClient(BASE) as c:
        c.login(username="admin", password="Password1")
        assert c._jwt == "jwt-snake"


@respx.mock(base_url=BASE)
def test_auth_required_for_libraries(respx_mock):
    """Calling list_libraries without login must raise — guard against
    a future refactor that lets unauthenticated calls through."""
    with BookOrbitClient(BASE) as c, pytest.raises(BookOrbitError, match="not authenticated"):
        c.list_libraries()


@respx.mock(base_url=BASE)
def test_create_library_sends_correct_dto(respx_mock):
    """Confirms the DTO shape matches BookOrbit's CreateLibraryDto:
    name, icon, folders[], watch, organizationMode."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "j"})
    )
    route = respx_mock.post("/api/v1/libraries").mock(
        return_value=httpx.Response(201, json={"id": "lib-1"})
    )
    with BookOrbitClient(BASE) as c:
        c.login(username="admin", password="x")
        out = c.create_library(name="biblichor", icon="📚", folders=["/books"])
    assert out["id"] == "lib-1"
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "name": "biblichor",
        "icon": "📚",
        "folders": ["/books"],
        "watch": True,
        "organizationMode": "book_per_folder",
    }


@respx.mock(base_url=BASE)
def test_trigger_scan_accepts_202(respx_mock):
    """BookOrbit's scanner returns 202 (Accepted) — make sure we don't
    treat that as an error."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "j"})
    )
    respx_mock.post("/api/v1/scanner/libraries/lib-1/scan").mock(return_value=httpx.Response(202))
    with BookOrbitClient(BASE) as c:
        c.login(username="admin", password="x")
        c.trigger_scan("lib-1")


# ============ ensure_bookorbit_ready (idempotency contract) ============


@respx.mock(base_url=BASE)
def test_ensure_creates_admin_and_library_on_first_run(respx_mock, tmp_path):
    """Phase 6m.ii: config.yaml is the single source of truth."""
    from endless_library.config import Config, load_config, save_config

    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": True})
    )
    respx_mock.post("/api/v1/auth/setup").mock(return_value=httpx.Response(201, json={"id": "u1"}))
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "j"})
    )
    respx_mock.get("/api/v1/libraries").mock(return_value=httpx.Response(200, json=[]))
    respx_mock.post("/api/v1/libraries").mock(
        return_value=httpx.Response(201, json={"id": "lib-new"})
    )

    cfg_yaml = tmp_path / "config.yaml"
    save_config(Config(), cfg_yaml)

    result = ensure_bookorbit_ready(
        url=BASE,
        setup_token="t",
        admin_username="admin",
        admin_name="Admin",
        admin_email="a@x.com",
        admin_password="Password1",
        library_root="/var/lib/biblichor/library",
        biblichor_config_yaml_path=cfg_yaml,
    )
    assert result.just_created is True
    assert result.library_id == "lib-new"
    # config.yaml is now the source of truth — bookorbit.json no longer exists
    reloaded = load_config(cfg_yaml)
    assert reloaded.bookorbit.library_id == "lib-new"
    assert reloaded.bookorbit.url == BASE
    assert reloaded.bookorbit.enabled is True


@respx.mock(base_url=BASE, assert_all_called=False)
def test_ensure_idempotent_on_second_run(respx_mock, tmp_path):
    """Second call: needsSetup=False, library already exists.
    Must NOT call /auth/setup or /libraries (POST) again."""
    from endless_library.config import Config, save_config

    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": False})
    )
    setup_route = respx_mock.post("/api/v1/auth/setup").mock(return_value=httpx.Response(409))
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "j"})
    )
    respx_mock.get("/api/v1/libraries").mock(
        return_value=httpx.Response(200, json=[{"id": "lib-existing", "name": "biblichor"}])
    )
    create_route = respx_mock.post("/api/v1/libraries").mock(return_value=httpx.Response(500))

    cfg_yaml = tmp_path / "config.yaml"
    save_config(Config(), cfg_yaml)

    result = ensure_bookorbit_ready(
        url=BASE,
        setup_token="t",
        admin_username="admin",
        admin_name="Admin",
        admin_email="a@x.com",
        admin_password="Password1",
        library_root="/var/lib/biblichor/library",
        biblichor_config_yaml_path=cfg_yaml,
    )
    assert result.just_created is False
    assert result.library_id == "lib-existing"
    # Neither destructive endpoint should have been called
    assert not setup_route.called
    assert not create_route.called


def test_set_secret_values_is_atomic_on_failure(tmp_path):
    """set_secret_values must roll back all changes when any insert fails
    (ultrareview I13). We verify atomicity by patching the connect helper to
    return a wrapper that raises on the second INSERT, then asserting that
    neither value was written."""
    import sqlite3
    from contextlib import contextmanager
    from unittest.mock import patch

    from endless_library.bookorbit.service import BookOrbitService
    from endless_library.config import Config
    from endless_library.db.schema import connect
    from endless_library.secrets_store import init_secrets_table

    db_path = tmp_path / "test.db"
    key_path = tmp_path / "restore.key"
    key_path.write_bytes(b"x" * 64)

    cfg = Config()
    svc = BookOrbitService(cfg, db_path, key_path)
    with connect(db_path) as conn:
        init_secrets_table(conn)

    # Track INSERT calls; raise on the 2nd one.
    insert_count = [0]
    real_conn_ref = [None]

    @contextmanager
    def _failing_connect(path):
        real_conn = sqlite3.connect(path)
        real_conn_ref[0] = real_conn
        orig_execute = real_conn.execute

        def tracking_execute(sql, params=()):
            if "INSERT INTO secrets" in sql:
                insert_count[0] += 1
                if insert_count[0] >= 2:
                    raise sqlite3.OperationalError("simulated second-insert failure")
            return orig_execute(sql, params)

        real_conn.execute = tracking_execute
        try:
            yield real_conn
        finally:
            real_conn.close()

    raised = False
    with patch("endless_library.db.schema.connect", _failing_connect):
        try:
            svc.set_secret_values({"k1": "v1", "k2": "v2"})
        except Exception:
            raised = True

    assert raised, "Expected an exception from the simulated second-insert failure"
    # Confirm neither key persisted (query directly via a real connection).
    with connect(db_path) as conn:
        row_k1 = conn.execute("SELECT name FROM secrets WHERE name=?", ("k1",)).fetchone()
        row_k2 = conn.execute("SELECT name FROM secrets WHERE name=?", ("k2",)).fetchone()
    assert row_k1 is None, "k1 was persisted despite rollback"
    assert row_k2 is None, "k2 was persisted despite rollback"


def test_set_secret_values_stores_all_on_success(tmp_path):
    """set_secret_values must atomically persist all provided values (ultrareview I13)."""
    from endless_library.bookorbit.service import BookOrbitService
    from endless_library.config import Config
    from endless_library.db.schema import connect
    from endless_library.secrets_store import init_secrets_table

    db_path = tmp_path / "test.db"
    key_path = tmp_path / "restore.key"
    key_path.write_bytes(b"y" * 64)

    cfg = Config()
    svc = BookOrbitService(cfg, db_path, key_path)
    with connect(db_path) as conn:
        init_secrets_table(conn)

    svc.set_secret_values({"alpha": "hello", "beta": "world"})

    assert svc.get_secret_value("alpha") == "hello"
    assert svc.get_secret_value("beta") == "world"
