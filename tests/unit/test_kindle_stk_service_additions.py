"""Phase STK-recovery: additional tests for urllib/APIError exception translation."""
from __future__ import annotations

import urllib.error

import pytest

from endless_library.kindle_stk import (
    KindleStkAuthExpired,
    KindleStkUploadFailed,
)
from tests._stkclient_stub import FakeDevice, FakeVendoredClient


# ---------------------------------------------------------------------------
# Shared fixture (mirrors fake_bookorbit_svc in main test file)
# ---------------------------------------------------------------------------

@pytest.fixture
def bsvc():
    """Minimal BookOrbit service stub."""
    class _Svc:
        def __init__(self):
            self._secrets: dict = {}

        def get_secret_value(self, key):
            return self._secrets.get(key)

        def set_secret_value(self, key, value):
            self._secrets[key] = value

        def set_secret_values(self, kv):
            self._secrets.update(kv)

        def delete_secret_value(self, key):
            self._secrets.pop(key, None)

    return _Svc()


def _configure(bsvc):
    """Stamp minimal credentials so is_configured() returns True."""
    bsvc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    bsvc.set_secret_value("kindle_stk.adp_token", "ADP")
    bsvc.set_secret_value("kindle_stk.default_destination_sn", "G0WEB1")
    bsvc.set_secret_value("kindle_stk.default_destination_name", "Kindle for Web")


# ---------------------------------------------------------------------------
# send_file tests
# ---------------------------------------------------------------------------

def test_send_file_translates_urllib_403_to_auth_expired(bsvc, monkeypatch, tmp_path):
    """A urllib.error.HTTPError 403 raised by stkclient.send_file must map
    to KindleStkAuthExpired (not bubble as Unexpected)."""
    _configure(bsvc)

    urllib_err = urllib.error.HTTPError(
        url="https://stkservice.amazon.com/",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=None,
    )

    fake_client = FakeVendoredClient(send_raises=urllib_err)
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )

    f = tmp_path / "book.epub"
    f.write_bytes(b"fake")

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(bsvc)
    with pytest.raises(KindleStkAuthExpired):
        svc.send_file(f, format="EPUB", title="Test", author="Auth")


def test_send_file_translates_api_error_with_devicetoken_message_to_auth_expired(
    bsvc, monkeypatch, tmp_path
):
    """An APIError whose str contains 'DeviceInfoToken' must map to
    KindleStkAuthExpired regardless of whether we can parse an HTTP status."""
    _configure(bsvc)

    from endless_library.kindle_stk._vendored._api import APIError

    api_err = APIError(
        "HTTP Error 403: Forbidden",
        b'{"Message": "Failed to validate DeviceInfoToken."}',
    )

    fake_client = FakeVendoredClient(send_raises=api_err)
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )

    f = tmp_path / "book.epub"
    f.write_bytes(b"fake")

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(bsvc)
    with pytest.raises(KindleStkAuthExpired, match="DeviceInfoToken|Re-OAuth"):
        svc.send_file(f, format="EPUB", title="Test", author="Auth")


# ---------------------------------------------------------------------------
# list_devices tests
# ---------------------------------------------------------------------------

def test_list_devices_translates_urllib_403_to_auth_expired(bsvc, monkeypatch):
    """A urllib.error.HTTPError 403 from get_owned_devices must surface as
    KindleStkAuthExpired so /api/kindle-stk/devices returns a typed error
    instead of crashing with an unhandled exception."""
    bsvc.set_secret_value("kindle_stk.device_cert.pem", "PEM")
    bsvc.set_secret_value("kindle_stk.adp_token", "ADP")

    urllib_err = urllib.error.HTTPError(
        url="https://stkservice.amazon.com/",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=None,
    )

    class _FakeClientRaisesOnDevices:
        def get_owned_devices(self):
            raise urllib_err

    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: _FakeClientRaisesOnDevices(),
    )

    from endless_library.kindle_stk.service import KindleStkService
    svc = KindleStkService(bsvc)
    with pytest.raises(KindleStkAuthExpired):
        svc.list_devices()
