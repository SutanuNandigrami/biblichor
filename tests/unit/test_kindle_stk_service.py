"""Phase STK 4: KindleStkService unit tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.kindle_stk import (
    KindleStkAuthExpired,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)
from tests._stkclient_stub import FakeDevice, FakeOAuth2, FakeVendoredClient, register_fake


@pytest.fixture
def fake_bookorbit_svc(tmp_path):
    """Minimal stand-in for BookOrbitService."""
    class _Svc:
        def __init__(self):
            self._secrets: dict[str, str] = {}

        def get_secret_value(self, key: str) -> str | None:
            return self._secrets.get(key)

        def set_secret_value(self, key: str, value: str) -> None:
            self._secrets[key] = value

        def set_secret_values(self, kv: dict[str, str]) -> None:
            self._secrets.update(kv)

        def delete_secret_value(self, key: str) -> None:
            self._secrets.pop(key, None)

    return _Svc()


def test_is_configured_false_when_no_cert_stored(fake_bookorbit_svc):
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    assert svc.is_configured() is False


def test_is_configured_true_after_storing_cert_and_token(fake_bookorbit_svc):
    from endless_library.kindle_stk.service import KindleStkService
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    svc = KindleStkService(fake_bookorbit_svc)
    assert svc.is_configured() is True


def test_start_oauth_returns_url_and_stashes_verifier(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    url, verifier = svc.start_oauth()
    assert url.startswith("https://www.amazon.com/ap/oa?")
    assert verifier == "fake_code_verifier_FAKEFAKE"
    assert fake_bookorbit_svc.get_secret_value(
        "kindle_stk.oauth_state.code_verifier"
    ) == "fake_code_verifier_FAKEFAKE"


def test_complete_oauth_extracts_code_and_persists_cert(fake_bookorbit_svc, monkeypatch):
    """Happy path: redirect URL parses, fake vendored Client.register_device
    returns a cert + ADP token, KindleStkService persists everything and
    deletes the ephemeral verifier."""
    fake_client = FakeVendoredClient()
    fake_client.register_device = lambda code, verifier, domain="amazon.com": {
        "device_private_key": "PEM_KEY",
        "adp_token": "ADP_TOK",
        "adp_did": "DID-1",
        "customer_id": "amzn1.account.xyz",
        "customer_name": "Sutanu N.",
    }
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.OAuth2",
        FakeOAuth2,
    )
    fake_bookorbit_svc.set_secret_value(
        "kindle_stk.oauth_state.code_verifier", "fake_verifier"
    )

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    result = svc.complete_oauth(
        "https://www.amazon.com/ap/maplanding?openid.oa2.access_token=AUTHCODE&foo=bar"
    )
    assert result["customer_id"] == "amzn1.account.xyz"
    assert result["customer_name"] == "Sutanu N."
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.device_cert.pem") == "PEM_KEY"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.adp_token") == "ADP_TOK"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.adp_did") == "DID-1"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.amazon_customer_id") == "amzn1.account.xyz"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.oauth_state.code_verifier") is None


def test_complete_oauth_raises_on_malformed_redirect_url(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(ValueError, match="redirect URL"):
        svc.complete_oauth("https://example.com/no-code-here")


def test_list_devices_returns_owned_devices(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    devices = svc.list_devices()
    assert len(devices) >= 1
    assert devices[0].device_serial_number == "G0WEB1"


def test_list_devices_raises_not_configured_when_no_cert(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(KindleStkNotConfigured):
        svc.list_devices()


def test_set_default_destination_validates_against_device_list(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    svc.set_default_destination("G0WEB1")
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.default_destination_sn") == "G0WEB1"
    assert fake_bookorbit_svc.get_secret_value("kindle_stk.default_destination_name") == "Kindle for Web"


def test_set_default_destination_rejects_unknown_device_sn(fake_bookorbit_svc, monkeypatch):
    register_fake(monkeypatch)
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(ValueError, match="unknown device"):
        svc.set_default_destination("UNKNOWN-SN-9999")


def test_send_file_raises_not_configured_when_no_cert(fake_bookorbit_svc, monkeypatch, tmp_path):
    register_fake(monkeypatch)
    f = tmp_path / "x.epub"
    f.write_bytes(b"x")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(KindleStkNotConfigured):
        svc.send_file(f, format="EPUB", title="t", author="a")


def test_send_file_translates_vendored_409_to_rate_limited(fake_bookorbit_svc, monkeypatch, tmp_path):
    import requests
    class _R:
        status_code = 429
        headers = {"Retry-After": "30"}
    err = requests.HTTPError(response=_R())
    fake_client = FakeVendoredClient(send_raises=err)
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    fake_bookorbit_svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    fake_bookorbit_svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    f = tmp_path / "x.epub"
    f.write_bytes(b"x")

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(KindleStkRateLimited) as exc:
        svc.send_file(f, format="EPUB", title="t", author="a")
    assert exc.value.retry_after_sec == 30


def test_send_file_translates_401_to_auth_expired(fake_bookorbit_svc, monkeypatch, tmp_path):
    import requests
    class _R:
        status_code = 401
        headers = {}
    err = requests.HTTPError(response=_R())
    fake_client = FakeVendoredClient(send_raises=err)
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    fake_bookorbit_svc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    fake_bookorbit_svc.set_secret_value("kindle_stk.adp_token", "ADP")
    fake_bookorbit_svc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    fake_bookorbit_svc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")
    f = tmp_path / "x.epub"
    f.write_bytes(b"x")

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    with pytest.raises(KindleStkAuthExpired):
        svc.send_file(f, format="EPUB", title="t", author="a")


def test_deregister_wipes_all_secrets(fake_bookorbit_svc, monkeypatch):
    """deregister() must remove every kindle_stk.* key."""
    register_fake(monkeypatch)
    keys = [
        "kindle_stk.device_cert.pem", "kindle_stk.adp_token", "kindle_stk.adp_did",
        "kindle_stk.amazon_customer_id", "kindle_stk.default_destination_sn",
        "kindle_stk.default_destination_name", "kindle_stk.registered_at",
    ]
    for k in keys:
        fake_bookorbit_svc.set_secret_value(k, "value")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    svc.deregister()
    for k in keys:
        assert fake_bookorbit_svc.get_secret_value(k) is None


def test_start_oauth_always_uses_amazon_com_url_even_for_india(fake_bookorbit_svc, monkeypatch):
    """Even when kindle_stk.amazon_domain is set to amazon.in, the authorize URL
    must use www.amazon.com — Amazon's OAuth/STK paths are centralised on .com.
    The domain only affects the token-exchange x-amzn-identity-auth-domain header."""
    from tests._stkclient_stub import FakeOAuth2
    monkeypatch.setattr("endless_library.kindle_stk._vendored.OAuth2", FakeOAuth2)
    fake_bookorbit_svc.set_secret_value("kindle_stk.amazon_domain", "amazon.in")
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    url, verifier = svc.start_oauth()
    assert "amazon.com" in url, f"Expected amazon.com in URL but got: {url}"
    assert "amazon.in" not in url.split("?")[0], f"URL base must NOT be amazon.in: {url}"


def test_start_oauth_defaults_to_amazon_com_when_no_region_set(fake_bookorbit_svc, monkeypatch):
    """When no kindle_stk.amazon_domain secret is stored, defaults to amazon.com."""
    from tests._stkclient_stub import FakeOAuth2
    monkeypatch.setattr("endless_library.kindle_stk._vendored.OAuth2", FakeOAuth2)
    # No amazon_domain set in secrets
    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(fake_bookorbit_svc)
    url, verifier = svc.start_oauth()
    assert "amazon.com" in url, f"Expected amazon.com in URL but got: {url}"

def test_create_oauth_url_always_returns_amazon_com_for_india():
    """BiblichorOAuth2.create_oauth_url(domain='amazon.in') must return a URL
    with www.amazon.com/ap/signin — NOT amazon.in."""
    from endless_library.kindle_stk._vendored import OAuth2
    url, verifier = OAuth2.create_oauth_url(domain="amazon.in")
    assert "www.amazon.com/ap/signin" in url, (
        f"URL base must be www.amazon.com/ap/signin, got: {url}"
    )
    assert "amazon.in" not in url.split("?")[0], (
        f"URL base must NOT contain amazon.in: {url}"
    )
    # return_to must also be amazon.com
    assert "openid.return_to=https%3A%2F%2Fwww.amazon.com" in url, (
        f"openid.return_to must be www.amazon.com: {url}"
    )


def test_create_oauth_url_always_returns_amazon_com_for_uk():
    """BiblichorOAuth2.create_oauth_url(domain='amazon.co.uk') must return
    www.amazon.com/ap/signin."""
    from endless_library.kindle_stk._vendored import OAuth2
    url, verifier = OAuth2.create_oauth_url(domain="amazon.co.uk")
    assert "www.amazon.com/ap/signin" in url, (
        f"URL base must be www.amazon.com/ap/signin, got: {url}"
    )
