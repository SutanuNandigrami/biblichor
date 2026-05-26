"""Vendored maxdjohnson/stkclient @ v0.1.1, MIT.

Re-export only the symbols biblichor uses. Internal modules are
underscore-prefixed so it's obvious which code is upstream.

This module wraps the upstream stkclient with biblichor's expected API:

- ``OAuth2`` exposes *static* ``create_oauth_url() -> (url, verifier)``
  and ``extract_code_from_redirect(url) -> code`` matching FakeOAuth2
  in tests/_stkclient_stub.py. Internally it delegates to the upstream
  ``_stkclient.OAuth2`` instance methods.

- ``Client`` exposes a biblichor-specific ``register_device(code, verifier)``
  that runs the full OAuth token-exchange + FirsProxy registration and
  returns a plain dict. It also wraps ``get_owned_devices()`` and
  ``send_file()`` so the caller never sees upstream internals.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import _api as _apimod
from ._model import DeviceInfo, OwnedDevice
from ._stkclient import Client as _UpstreamClient
from ._stkclient import OAuth2 as _UpstreamOAuth2  # noqa: F401
from ._stkclient import _parse_authorization_code


class OAuth2:
    """Biblichor adapter over upstream stkclient.OAuth2.

    All methods are static so the test stub (FakeOAuth2) can replace this
    class wholesale via monkeypatch without needing an instance.
    """

    @staticmethod
    def create_oauth_url(domain: str = "amazon.com") -> tuple[str, str]:
        """Return (authorize_url, code_verifier).

        The ``domain`` parameter is accepted for API compatibility but the
        user-facing OAuth URL is ALWAYS hardcoded to www.amazon.com.
        Amazon's OAuth and Send-to-Kindle paths are centralised on amazon.com
        regardless of the user's account region (amazon.in, amazon.co.uk, etc.).
        The domain is only used for the x-amzn-identity-auth-domain header in
        the token-exchange step (see _token_exchange_with_domain).
        """
        import base64
        import hashlib
        import os
        import urllib.parse

        verifier = base64.b64encode(os.urandom(32), b"-_").rstrip(b"=").decode("utf8")
        challenge = base64.b64encode(
            hashlib.sha256(verifier.encode("utf-8")).digest(), b"-_"
        ).rstrip(b"=").decode("utf8")

        q = {
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.ns.oa2": "http://www.amazon.com/ap/ext/oauth/2",
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.oa2.client_id": "device:658490dfb190e494030082836775981fa23be0c2425441860352ba0f55915b43002d",
            "openid.mode": "checkid_setup",
            "openid.oa2.scope": "device_auth_access",
            "openid.oa2.response_type": "code",
            "openid.oa2.code_challenge": challenge,
            "openid.oa2.code_challenge_method": "S256",
            "openid.return_to": "https://www.amazon.com/gp/sendtokindle",
            "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
            "openid.pape.max_auth_age": "0",
            "accountStatusPolicy": "P1",
            "openid.assoc_handle": "amzn_device_na",
            "pageId": "amzn_device_common_dark",
            "disableLoginPrepopulate": "1",
        }
        url = "https://www.amazon.com/ap/signin?" + urllib.parse.urlencode(q)
        return url, verifier

    @staticmethod
    def extract_code_from_redirect(redirect_url: str) -> str:
        """Parse the authorization code out of Amazon's OAuth2 redirect URL.

        Raises ValueError if no code is found.
        """
        try:
            return _parse_authorization_code(redirect_url)
        except (KeyError, IndexError) as e:
            raise ValueError(
                f"Could not extract authorization code from redirect URL: {redirect_url!r}"
            ) from e


def _token_exchange_with_domain(authorization_code: str, code_verifier: str, domain: str = "amazon.com") -> str:
    """Re-implementation of _api.token_exchange with a regional auth-domain header.

    For non-US Amazon regions the x-amzn-identity-auth-domain header must use
    the regional API hostname (e.g. api.amazon.in). The upstream _api.token_exchange
    hardcodes api.amazon.com so we reimplement it here.
    """
    import json
    import urllib.error
    import urllib.request

    auth_api_host = f"api.{domain}"
    body = {
        "app_name": "Unknown",
        "client_domain": "DeviceLegacy",
        "client_id": "658490dfb190e494030082836775981fa23be0c2425441860352ba0f55915b43002d",
        "code_algorithm": "SHA-256",
        "code_verifier": code_verifier,
        "requested_token_type": "access_token",
        "source_token": authorization_code,
        "source_token_type": "authorization_code",
    }
    req = urllib.request.Request(
        url=f"https://{auth_api_host}/auth/token",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Accept-Language": "en-US",
            "x-amzn-identity-auth-domain": auth_api_host,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body_bytes = e.read()
        except AttributeError:
            body_bytes = None
        raise _apimod.APIError(str(e), body_bytes) from e
    access_token: str = res["access_token"]
    return access_token


class Client:
    """Biblichor wrapper over upstream stkclient.Client.

    Provides a biblichor-friendly API on top of the upstream dataclass.
    The internal representation is a serialized DeviceInfo JSON string so
    we can persist credentials to the secrets store across process restarts.
    """

    def __init__(self, _upstream: _UpstreamClient | None = None) -> None:
        self._upstream = _upstream

    # ---------- OAuth registration ----------

    def register_device(self, code: str, verifier: str, domain: str = "amazon.com") -> dict[str, Any]:
        """Exchange authorization code + verifier for a registered device.

        Returns a dict with keys:
            device_private_key, adp_token, adp_did, customer_id,
            customer_name, device_type, given_name, name.

        Also stores the resulting upstream Client so the same instance can
        be used for subsequent calls.

        ``domain`` selects the regional Amazon auth endpoint, e.g. "amazon.in"
        for India. The x-amzn-identity-auth-domain header in the token exchange
        must match the domain used for the OAuth sign-in URL.
        """
        access_token = _token_exchange_with_domain(code, verifier, domain)
        device_info: DeviceInfo = _apimod.register_device_with_token(access_token)
        self._upstream = _UpstreamClient(device_info)
        return {
            "device_private_key": device_info.device_private_key,
            "adp_token": device_info.adp_token,
            "adp_did": device_info.user_directed_id,
            "customer_id": device_info.user_directed_id,
            "customer_name": device_info.given_name or device_info.name,
            "device_type": device_info.device_type,
            "given_name": device_info.given_name,
            "name": device_info.name,
        }

    # ---------- Serialisation (for credential persistence) ----------

    def dumps(self) -> str:
        """Serialise the upstream client to a JSON string for storage."""
        if self._upstream is None:
            raise RuntimeError("Client not yet registered; call register_device first")
        return self._upstream.dumps()

    @classmethod
    def loads(cls, s: str) -> Client:
        """Deserialise a Client from a JSON string previously returned by dumps()."""
        upstream = _UpstreamClient.loads(s)
        return cls(upstream)

    # ---------- Device management ----------

    def get_owned_devices(self) -> list[OwnedDevice]:
        """Return a list of OwnedDevice objects."""
        if self._upstream is None:
            raise RuntimeError("Client not initialised")
        return self._upstream.get_owned_devices()

    # ---------- Send ----------

    def send_file(
        self,
        file_path: Path,
        *,
        destinations: list,
        format: str,
        title: str,
        author: str,
    ) -> dict:
        """Send a file to one or more devices.

        ``destinations`` is a list of objects with a ``device_serial_number``
        attribute (OwnedDevice or FakeDevice). Translates to the upstream
        ``target_device_serial_numbers`` list internally.
        """
        if self._upstream is None:
            raise RuntimeError("Client not initialised")
        serials = [d.device_serial_number for d in destinations]
        sku = self._upstream.send_file(
            file_path,
            serials,
            format=format,
            title=title,
            author=author,
        )
        return {"sku": sku}

    # ---------- Logout ----------

    def logout(self) -> None:
        if self._upstream is not None:
            self._upstream.logout()


__all__ = ["Client", "OAuth2", "OwnedDevice"]
