"""Phase STK 9: FastAPI endpoints for setup wizard + status + test send."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.db.schema import init_db
from endless_library.web import api as api_mod
from tests._stkclient_stub import FakeOAuth2, FakeVendoredClient


def _build_app(tmp_path: Path) -> tuple[FastAPI, SimpleNamespace]:
    db = tmp_path / "test.db"
    init_db(db)
    app = FastAPI()
    # In-memory BookOrbitService stand-in
    class _Svc:
        def __init__(self): self._s: dict[str, str] = {}
        def get_secret_value(self, k): return self._s.get(k)
        def set_secret_value(self, k, v): self._s[k] = v
        def set_secret_values(self, kv): self._s.update(kv)
        def delete_secret_value(self, k): self._s.pop(k, None)
    svc = _Svc()
    deps = SimpleNamespace(
        db_path=db,
        cfg=SimpleNamespace(stk=SimpleNamespace(daily_cap=500)),
        bookorbit_service=svc,
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.deps = deps
    app.state.scheduler = SimpleNamespace(running=True)
    app.state.config_path = Path("/tmp/cfg.yaml")
    api_mod.register(app)
    return app, svc


def test_status_returns_configured_false_when_no_setup(tmp_path):
    app, _ = _build_app(tmp_path)
    r = TestClient(app).get("/api/kindle-stk/status")
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_status_returns_full_block_when_configured(tmp_path):
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.amazon_customer_id", "amzn1.account.x")
    svc.set_secret_value("kindle_stk.registered_at", "2026-05-23T12:00:00+00:00")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    r = TestClient(app).get("/api/kindle-stk/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["customer_id"] == "amzn1.account.x"
    assert body["default_destination"] == "Kindle for Web"


def test_oauth_start_returns_authorize_url(tmp_path, monkeypatch):
    monkeypatch.setattr("endless_library.kindle_stk._vendored.OAuth2", FakeOAuth2)
    app, _ = _build_app(tmp_path)
    r = TestClient(app).post("/api/kindle-stk/oauth/start")
    assert r.status_code == 200
    body = r.json()
    assert body["authorize_url"].startswith("https://www.amazon.com/ap/oa?")


def test_oauth_complete_with_valid_url_persists_cert(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    fake_client.register_device = lambda c, v: {
        "device_private_key": "PEM_K", "adp_token": "ADP_T", "adp_did": "DID-1",
        "customer_id": "amzn1.account.y", "customer_name": "Test",
    }
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    monkeypatch.setattr("endless_library.kindle_stk._vendored.OAuth2", FakeOAuth2)
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.oauth_state.code_verifier", "verifier")
    r = TestClient(app).post(
        "/api/kindle-stk/oauth/complete",
        json={"redirect_url": "https://www.amazon.com/ap/maplanding?openid.oa2.access_token=XYZ"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["customer_id"] == "amzn1.account.y"
    assert svc.get_secret_value("kindle_stk.device_cert.pem") == "PEM_K"


def test_oauth_complete_with_invalid_url_returns_400(tmp_path, monkeypatch):
    monkeypatch.setattr("endless_library.kindle_stk._vendored.OAuth2", FakeOAuth2)
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.oauth_state.code_verifier", "verifier")
    r = TestClient(app).post(
        "/api/kindle-stk/oauth/complete",
        json={"redirect_url": "https://example.com/garbage"},
    )
    assert r.status_code == 400


def test_devices_returns_list_after_configured(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    r = TestClient(app).get("/api/kindle-stk/devices")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["devices"], list)
    assert any(d["device_serial_number"] == "G0WEB1" for d in body["devices"])


def test_default_destination_validates_against_device_list(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    r = TestClient(app).put(
        "/api/kindle-stk/default-destination",
        json={"device_sn": "G0WEB1"},
    )
    assert r.status_code == 200
    assert svc.get_secret_value("kindle_stk.default_destination_sn") == "G0WEB1"


def test_default_destination_returns_400_for_unknown_sn(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    r = TestClient(app).put(
        "/api/kindle-stk/default-destination",
        json={"device_sn": "BOGUS-SN"},
    )
    assert r.status_code == 400


def test_delete_connection_wipes_secrets(tmp_path, monkeypatch):
    fake_client = FakeVendoredClient()
    fake_client.disown_device = lambda: None
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    r = TestClient(app).delete("/api/kindle-stk/connection")
    assert r.status_code == 200
    assert svc.get_secret_value("kindle_stk.device_cert.pem") is None


def test_test_send_returns_4xx_on_send_failure(tmp_path, monkeypatch):
    from endless_library.kindle_stk import KindleStkUploadFailed
    fake_client = FakeVendoredClient(send_raises=KindleStkUploadFailed("nope"))
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    app, svc = _build_app(tmp_path)
    svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    r = TestClient(app).post("/api/kindle-stk/test-send")
    assert r.status_code in (400, 502)
