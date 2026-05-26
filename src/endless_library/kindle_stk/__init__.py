"""Biblichor's Send-to-Kindle browser-upload integration.

The public surface is small: KindleStkService for OAuth + send, and
the typed exception hierarchy. The pipeline does not import this
module directly -- it goes through kindle_router.deliver(...).
"""
from .exceptions import (
    KindleStkAuthExpired,
    KindleStkBatchOverflow,
    KindleStkError,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkUploadFailed,
)
from .service import KindleStkService

__all__ = [
    "KindleStkAuthExpired",
    "KindleStkBatchOverflow",
    "KindleStkError",
    "KindleStkNotConfigured",
    "KindleStkRateLimited",
    "KindleStkService",
    "KindleStkUploadFailed",
]
