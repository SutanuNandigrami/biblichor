"""Fake vendored stkclient.Client + OAuth2 for biblichor unit tests.

Used by tests that exercise KindleStkService and kindle_router without
hitting Amazon. Mirrors the surface of endless_library.kindle_stk._vendored.

Configure behaviour via constructor kwargs:
    FakeVendoredClient(
        devices=[FakeDevice(...)],
        send_raises=KindleStkUploadFailed('..'),
    )
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FakeDevice:
    device_serial_number: str
    device_type: str
    device_name: str


class FakeVendoredClient:
    """Stand-in for endless_library.kindle_stk._vendored.Client."""

    def __init__(
        self,
        *,
        devices: list[FakeDevice] | None = None,
        send_raises: Exception | None = None,
        send_return: dict | None = None,
    ):
        self._devices = devices or [
            FakeDevice(
                device_serial_number="G0WEB1",
                device_type="FionaWebApp",
                device_name="Kindle for Web",
            ),
        ]
        self._send_raises = send_raises
        self._send_return = send_return or {"transaction_id": "tx-1", "status": "PENDING"}
        self.send_calls: list[dict] = []

    def get_owned_devices(self) -> list[FakeDevice]:
        return list(self._devices)

    def send_file(
        self,
        file_path,
        *,
        destinations: list[FakeDevice],
        format: str,
        title: str,
        author: str,
    ) -> dict:
        self.send_calls.append(
            {
                "file_path": str(file_path),
                "destinations": [d.device_serial_number for d in destinations],
                "format": format,
                "title": title,
                "author": author,
            }
        )
        if self._send_raises is not None:
            raise self._send_raises
        return self._send_return


class FakeOAuth2:
    """Stand-in for endless_library.kindle_stk._vendored.OAuth2."""

    @staticmethod
    def create_oauth_url(domain: str = "amazon.com") -> tuple[str, str]:
        """Returns (authorize_url, code_verifier).

        Mirrors the real OAuth2.create_oauth_url(domain=...) signature.
        The domain parameter is accepted but the URL is always amazon.com,
        matching the corrected real implementation.
        """
        return (
            "https://www.amazon.com/ap/oa?client_id=stk&scope=&response_type=code&"
            "redirect_uri=https%3A%2F%2Fwww.amazon.com%2Fap%2Fmaplanding&"
            "code_challenge=fake_challenge&code_challenge_method=S256",
            "fake_code_verifier_FAKEFAKE",
        )

    @staticmethod
    def extract_code_from_redirect(redirect_url: str) -> str:
        """Real OAuth2 parses the URL fragment. Fake just looks for a code= param."""
        if "openid.oa2.access_token=" in redirect_url:
            return redirect_url.split("openid.oa2.access_token=", 1)[1].split("&", 1)[0]
        if "code=" in redirect_url:
            return redirect_url.split("code=", 1)[1].split("&", 1)[0]
        raise ValueError(f"no code in redirect URL: {redirect_url}")


def register_fake(monkeypatch) -> tuple[FakeVendoredClient, type[FakeOAuth2]]:
    """Convenience helper for tests: patch the vendored module so
    'KindleStkService' uses fakes. Returns the fake instances so the
    test can assert on send_calls etc.

    Example:
        client, oauth = register_fake(monkeypatch)
        svc = KindleStkService(real_bookorbit_svc)
        svc.send_file(...)
        assert client.send_calls[0]['title'] == '...'
    """
    fake_client = FakeVendoredClient()
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.Client",
        lambda *a, **kw: fake_client,
    )
    monkeypatch.setattr(
        "endless_library.kindle_stk._vendored.OAuth2",
        FakeOAuth2,
    )
    return fake_client, FakeOAuth2
