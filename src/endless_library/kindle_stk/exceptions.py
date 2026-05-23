"""Biblichor's exception hierarchy for Send-to-Kindle.

Maps vendored stkclient's raw exceptions (requests.HTTPError, ValueError)
to typed biblichor exceptions so callers can pattern-match.
"""
from __future__ import annotations


class KindleStkError(Exception):
    """Base for all Send-to-Kindle errors."""


class KindleStkNotConfigured(KindleStkError):
    """No device cert in secrets store. User must complete OAuth setup."""


class KindleStkAuthExpired(KindleStkError):
    """ADP signing failed or device cert was revoked. User must re-OAuth."""


class KindleStkRateLimited(KindleStkError):
    """Amazon returned 429. Honors Retry-After header where present."""

    def __init__(self, message: str = '', *, retry_after_sec: int = 5) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class KindleStkUploadFailed(KindleStkError):
    """Transient or unknown failure during the 4-step send flow."""
