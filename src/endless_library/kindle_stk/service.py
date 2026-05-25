"""KindleStkService -- biblichor's facade over the vendored stkclient.

Handles all credential persistence via BookOrbitService. Translates
raw stkclient exceptions to biblichor's typed hierarchy. Provides
the public surface used by the FastAPI endpoints + kindle_router.

Credential persistence strategy
--------------------------------
After successful OAuth registration the vendored Client's state is
serialised to JSON via ``client.dumps()`` and stored as a single secret
(``kindle_stk.client_json``). In addition we store the individual
fields that the UI / healthz endpoint want to display without needing
to round-trip to Amazon:

    kindle_stk.device_cert.pem        -- device_private_key (PEM string)
    kindle_stk.adp_token              -- ADP token
    kindle_stk.adp_did                -- adp_did / user_directed_id
    kindle_stk.amazon_customer_id     -- Amazon customer ID
    kindle_stk.registered_at          -- ISO-8601 timestamp

Device selection
-----------------
    kindle_stk.default_destination_sn   -- device_serial_number of chosen device
    kindle_stk.default_destination_name -- human-readable name for display

Ephemeral OAuth state
----------------------
    kindle_stk.oauth_state.code_verifier -- PKCE verifier, deleted after exchange
"""
from __future__ import annotations

import dataclasses
import logging
import re
import time
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from . import _vendored
from .exceptions import (
    KindleStkAuthExpired,
    KindleStkBatchOverflow,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)

log = logging.getLogger(__name__)

DEVICES_CACHE_TTL_SEC = 300
STK_MAX_BATCH_BYTES = 200 * 1024 * 1024  # Amazon 200 MB per-call hard cap


@dataclasses.dataclass
class FileEntry:
    """One file to be delivered in a multi-file STK batch."""

    path: Path
    title: str
    author: str
    format: str


class KindleStkService:
    """High-level Send-to-Kindle facade.

    Each method that talks to Amazon reconstructs a fresh vendored
    Client from the stored credentials. We don't cache the Client object
    itself -- it's cheap to instantiate and avoids stale-state bugs across
    credential rotations.
    """

    def __init__(self, bookorbit_svc, *, per_file_interval_sec: float = 0.0) -> None:
        self._svc = bookorbit_svc
        self._devices_cache: tuple[list, float] | None = None
        self._per_file_interval_sec = per_file_interval_sec

    # ---- Configuration state ----

    def is_configured(self) -> bool:
        """Return True iff both device cert and ADP token are present."""
        cert = self._svc.get_secret_value("kindle_stk.device_cert.pem")
        adp = self._svc.get_secret_value("kindle_stk.adp_token")
        return bool(cert and adp)

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise KindleStkNotConfigured(
                "No device certificate stored. Complete OAuth setup first."
            )

    # ---- OAuth flow ----

    def start_oauth(self) -> tuple[str, str]:
        """Return (authorize_url, code_verifier).

        The verifier is also persisted to secrets so complete_oauth can
        pick it up after the user pastes the redirect URL back.

        The Amazon domain is read from the ``kindle_stk.amazon_domain`` secret
        (set via PUT /api/kindle-stk/region). Defaults to ``amazon.com``.
        """
        domain = self._svc.get_secret_value("kindle_stk.amazon_domain") or "amazon.com"
        url, verifier = _vendored.OAuth2.create_oauth_url(domain=domain)
        self._svc.set_secret_value("kindle_stk.oauth_state.code_verifier", verifier)
        return url, verifier

    def complete_oauth(self, redirect_url: str) -> dict[str, Any]:
        """Exchange code for tokens, register device, persist cert.

        Args:
            redirect_url: The full Amazon OAuth2 redirect URL the user
                          received after granting access.

        Returns:
            dict with keys: customer_id, customer_name, registered_at.

        Raises:
            ValueError: redirect URL is malformed or no code_verifier in session.
            KindleStkUploadFailed: registration HTTP call failed.
        """
        try:
            code = _vendored.OAuth2.extract_code_from_redirect(redirect_url)
        except ValueError as e:
            raise ValueError(f"Could not parse redirect URL: {e}") from e

        verifier = self._svc.get_secret_value("kindle_stk.oauth_state.code_verifier")
        if not verifier:
            raise ValueError(
                "No code_verifier in session. start_oauth must run before complete_oauth."
            )

        domain = self._svc.get_secret_value("kindle_stk.amazon_domain") or "amazon.com"
        client = _vendored.Client()
        try:
            result = client.register_device(code, verifier, domain=domain)
        except requests.HTTPError as e:
            raise KindleStkUploadFailed(f"register_device failed: {e}") from e

        now_iso = datetime.now(timezone.utc).isoformat()
        self._svc.set_secret_values(
            {
                "kindle_stk.device_cert.pem": result["device_private_key"],
                "kindle_stk.adp_token": result["adp_token"],
                "kindle_stk.adp_did": result.get("adp_did", ""),
                "kindle_stk.amazon_customer_id": result.get("customer_id", ""),
                "kindle_stk.registered_at": now_iso,
            }
        )
        try:
            self._svc.set_secret_value("kindle_stk.client_json", client.dumps())
        except Exception:
            pass  # non-fatal

        self._svc.delete_secret_value("kindle_stk.oauth_state.code_verifier")

        return {
            "customer_id": result.get("customer_id", ""),
            "customer_name": result.get("customer_name", ""),
            "registered_at": now_iso,
        }

    # ---- Device management ----

    def list_devices(self) -> list:
        """Return a list of OwnedDevice (or FakeDevice in tests).

        5-minute cache to avoid hammering stkservice. Bypass with
        _flush_devices_cache() if you need a live list immediately.
        """
        self._require_configured()
        if self._devices_cache is not None:
            devices, ts = self._devices_cache
            if time.time() - ts < DEVICES_CACHE_TTL_SEC:
                return devices
        client = self._build_client()
        try:
            devices = client.get_owned_devices()
        except _vendored._api.APIError as e:
            self._raise_typed_from_api_error(e)
        except urllib.error.HTTPError as e:
            self._raise_typed_from_urllib_error(e)
        self._devices_cache = (devices, time.time())
        return devices

    def _flush_devices_cache(self) -> None:
        self._devices_cache = None

    def set_default_destination(self, device_sn: str) -> None:
        """Persist the chosen device serial number + name as the default.

        Raises ValueError if device_sn is not in the current device list.
        """
        devices = self.list_devices()
        match = next(
            (d for d in devices if d.device_serial_number == device_sn), None
        )
        if not match:
            raise ValueError(
                f"unknown device: {device_sn!r} is not in your device list"
            )
        self._svc.set_secret_values(
            {
                "kindle_stk.default_destination_sn": device_sn,
                "kindle_stk.default_destination_name": match.device_name,
            }
        )

    # ---- Send (single file) ----

    def send_file(
        self,
        file_path: Path,
        *,
        format: str,
        title: str,
        author: str,
    ) -> dict[str, Any]:
        """Send a single file to the user's default destination device.

        Translates raw stkclient exceptions to biblichor's typed ones.
        Raises KindleStkNotConfigured if no cert or no default device.
        """
        self._require_configured()
        dest_sn = self._svc.get_secret_value("kindle_stk.default_destination_sn")
        if not dest_sn:
            raise KindleStkNotConfigured(
                "No default destination device. Complete setup wizard first."
            )

        devices = self.list_devices()
        dest = next(
            (d for d in devices if d.device_serial_number == dest_sn), None
        )
        if not dest:
            raise KindleStkNotConfigured(
                f"Default device {dest_sn!r} is no longer registered. Re-pick a device."
            )

        client = self._build_client()
        try:
            return client.send_file(
                file_path,
                destinations=[dest],
                format=format,
                title=title,
                author=author,
            )
        except requests.HTTPError as e:
            self._raise_typed_from_http_error(e)
        except _vendored._api.APIError as e:
            self._raise_typed_from_api_error(e)
        except urllib.error.HTTPError as e:
            self._raise_typed_from_urllib_error(e)
        except Exception as e:
            raise KindleStkUploadFailed(f"Unexpected stkclient failure: {e}") from e

    # ---- Send (multi-file batch) ----

    def send_files(
        self,
        files: list[FileEntry],
        *,
        max_batch_bytes: int = STK_MAX_BATCH_BYTES,
    ) -> list[dict[str, Any]]:
        """Send multiple files to the default destination in one signed session.

        Each file is a FileEntry(path, title, author, format). Files are sent
        sequentially within a single signed session (one _build_client call,
        shared Signer), which avoids N separate cert challenge + throttle sleeps.

        Pre-flight validation:
          - Any single file > max_batch_bytes -> raises KindleStkBatchOverflow.
          - Total batch > max_batch_bytes -> raises KindleStkBatchOverflow.
            (kindle_router.deliver_batch is responsible for splitting; this is
            a safety net.)

        Returns a list of dicts, one per file: {sku, file_path}.

        Raises:
            KindleStkBatchOverflow: single file or total batch exceeds 200 MB.
            KindleStkNotConfigured: no cert or no default device.
            KindleStkAuthExpired, KindleStkRateLimited, KindleStkUploadFailed.
        """
        self._require_configured()
        dest_sn = self._svc.get_secret_value("kindle_stk.default_destination_sn")
        if not dest_sn:
            raise KindleStkNotConfigured(
                "No default destination device. Complete setup wizard first."
            )

        devices = self.list_devices()
        dest = next(
            (d for d in devices if d.device_serial_number == dest_sn), None
        )
        if not dest:
            raise KindleStkNotConfigured(
                f"Default device {dest_sn!r} is no longer registered. Re-pick a device."
            )

        # --- Pre-flight size checks ---
        total = 0
        for fe in files:
            sz = fe.path.stat().st_size
            if sz > max_batch_bytes:
                raise KindleStkBatchOverflow(
                    f"File {fe.path.name!r} ({sz // 1_048_576} MB) exceeds "
                    f"200 MB STK hard limit",
                    file_path=str(fe.path),
                    size_bytes=sz,
                )
            total += sz
        if total > max_batch_bytes:
            raise KindleStkBatchOverflow(
                f"Batch total {total // 1_048_576} MB exceeds 200 MB STK limit "
                f"({len(files)} files); caller should split into smaller chunks",
                size_bytes=total,
            )

        # Build one client (one signed session) for the whole batch.
        client = self._build_client()
        results: list[dict[str, Any]] = []
        for i, fe in enumerate(files):
            # Throttle between files inside a batch. Amazon anti-abuse sees
            # absolute request rate, not session boundaries — 8 files in 19s
            # (21:21:08-21:21:27) revoked the cert mid-session. Skip before
            # the first file: the router-level min_send_interval_sec already
            # enforces the inter-batch gap so we don't double-sleep there.
            if i > 0 and self._per_file_interval_sec > 0:
                time.sleep(self._per_file_interval_sec)
            try:
                ret = client.send_file(
                    fe.path,
                    destinations=[dest],
                    format=fe.format,
                    title=fe.title,
                    author=fe.author,
                )
                results.append({"sku": ret.get("sku", ""), "file_path": str(fe.path)})
            except requests.HTTPError as e:
                self._raise_typed_from_http_error(e)
            except _vendored._api.APIError as e:
                self._raise_typed_from_api_error(e)
            except urllib.error.HTTPError as e:
                self._raise_typed_from_urllib_error(e)
            except Exception as e:
                raise KindleStkUploadFailed(
                    f"Unexpected stkclient failure on {fe.path.name!r}: {e}"
                ) from e
        return results

    # ---- Exception translation helpers ----

    def _raise_typed_from_http_error(self, exc: requests.HTTPError) -> None:
        """Map a raw requests.HTTPError to biblichor's typed exception."""
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        if status in (401, 403):
            raise KindleStkAuthExpired(
                f"Amazon rejected the device cert (HTTP {status}). Re-OAuth required."
            ) from exc
        if status == 429:
            retry_after = 5
            if resp is not None:
                hdr = getattr(resp, "headers", {}) or {}
                try:
                    retry_after = int(hdr.get("Retry-After", "5"))
                except (TypeError, ValueError):
                    retry_after = 5
            raise KindleStkRateLimited(
                f"Rate-limited by Amazon (Retry-After {retry_after}s)",
                retry_after_sec=retry_after,
            ) from exc
        raise KindleStkUploadFailed(f"HTTP {status}: {exc}") from exc

    def _raise_typed_from_urllib_error(self, exc: urllib.error.HTTPError) -> None:
        """Map a raw urllib.error.HTTPError to biblichor's typed exception."""
        status = getattr(exc, "code", None)
        if status in (401, 403):
            raise KindleStkAuthExpired(
                f"Amazon rejected the device cert (HTTP {status}). Re-OAuth required."
            ) from exc
        if status == 429:
            raise KindleStkRateLimited(
                "Rate-limited by Amazon (HTTP 429)",
                retry_after_sec=5,
            ) from exc
        raise KindleStkUploadFailed(f"HTTP {status}: {exc}") from exc

    def _raise_typed_from_api_error(self, exc) -> None:
        """Map a vendored APIError to biblichor's typed exception.

        The APIError message may contain the raw JSON body, e.g.:
            'HTTP Error 403: Forbidden {"Message": "Failed to validate DeviceInfoToken."}'
        Parse the HTTP status from the str representation first; fall back to
        the 'DeviceInfoToken' text pattern as an auth-expired signal.
        """
        msg = str(exc)

        # Try to extract status code from "HTTP Error NNN:" prefix
        status: int | None = None
        m = re.search(r"HTTP Error (\d+)", msg)
        if m:
            status = int(m.group(1))

        if status in (401, 403) or "DeviceInfoToken" in msg:
            raise KindleStkAuthExpired(
                f"Amazon rejected the device cert (APIError). Re-OAuth required. detail={msg!r}"
            ) from exc
        if status == 429:
            raise KindleStkRateLimited(
                "Rate-limited by Amazon (APIError 429)",
                retry_after_sec=5,
            ) from exc
        raise KindleStkUploadFailed(f"APIError: {msg}") from exc

    # ---- Deregister ----

    def deregister(self) -> None:
        """Wipe every kindle_stk.* secret.

        Best-effort call to the upstream logout endpoint; failure of that
        call doesn't prevent the local wipe.
        """
        try:
            if self.is_configured():
                client = self._build_client()
                if hasattr(client, "logout"):
                    try:
                        client.logout()
                    except Exception as e:
                        log.warning("kindle_stk: logout failed: %s", e)
        finally:
            for k in (
                "kindle_stk.device_cert.pem",
                "kindle_stk.adp_token",
                "kindle_stk.adp_did",
                "kindle_stk.amazon_customer_id",
                "kindle_stk.registered_at",
                "kindle_stk.client_json",
                "kindle_stk.default_destination_sn",
                "kindle_stk.default_destination_name",
                "kindle_stk.oauth_state.code_verifier",
            ):
                self._svc.delete_secret_value(k)
            self._flush_devices_cache()

    # ---- Internal helpers ----

    def _build_client(self):
        """Construct a fresh vendored Client from stored credentials.

        When tests monkeypatch _vendored.Client to a lambda(*a, **kw),
        this method calls it with no args, which works because the lambda
        accepts **kw and ignores them.
        """
        client_json = self._svc.get_secret_value("kindle_stk.client_json")
        if client_json:
            try:
                return _vendored.Client.loads(client_json)
            except Exception:
                pass

        # Fallback: for tests (monkeypatched Client) or imported credentials.
        return _vendored.Client()
