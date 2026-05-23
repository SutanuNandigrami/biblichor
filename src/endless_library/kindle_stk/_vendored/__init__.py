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
from ._stkclient import OAuth2 as _UpstreamOAuth2
from ._stkclient import _parse_authorization_code


class OAuth2:
    """Biblichor adapter over upstream stkclient.OAuth2.

    All methods are static so the test stub (FakeOAuth2) can replace this
    class wholesale via monkeypatch without needing an instance.
    """

    @staticmethod
    def create_oauth_url() -> tuple[str, str]:
        """Return (authorize_url, code_verifier).

        Instantiates a fresh upstream OAuth2 internally (it generates the
        verifier in __init__), then calls get_signin_url() and exposes the
        verifier as the second element of the tuple.
        """
        o = _UpstreamOAuth2()
        url = o.get_signin_url()
        verifier = o._verifier
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


class Client:
    """Biblichor wrapper over upstream stkclient.Client.

    Provides a biblichor-friendly API on top of the upstream dataclass.
    The internal representation is a serialized DeviceInfo JSON string so
    we can persist credentials to the secrets store across process restarts.
    """

    def __init__(self, _upstream: _UpstreamClient | None = None) -> None:
        self._upstream = _upstream

    # ---------- OAuth registration ----------

    def register_device(self, code: str, verifier: str) -> dict[str, Any]:
        """Exchange authorization code + verifier for a registered device.

        Returns a dict with keys:
            device_private_key, adp_token, adp_did, customer_id,
            customer_name, device_type, given_name, name.

        Also stores the resulting upstream Client so the same instance can
        be used for subsequent calls.
        """
        access_token = _apimod.token_exchange(code, verifier)
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
    def loads(cls, s: str) -> "Client":
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
