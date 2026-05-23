"""Phase STK 2: biblichor's exception hierarchy for Send-to-Kindle."""
from __future__ import annotations

import pytest


def test_exceptions_form_a_hierarchy():
    from endless_library.kindle_stk.exceptions import (
        KindleStkError,
        KindleStkNotConfigured,
        KindleStkAuthExpired,
        KindleStkRateLimited,
        KindleStkUploadFailed,
    )
    assert issubclass(KindleStkNotConfigured, KindleStkError)
    assert issubclass(KindleStkAuthExpired, KindleStkError)
    assert issubclass(KindleStkRateLimited, KindleStkError)
    assert issubclass(KindleStkUploadFailed, KindleStkError)


def test_rate_limited_carries_retry_after_sec():
    from endless_library.kindle_stk.exceptions import KindleStkRateLimited
    e = KindleStkRateLimited('rate limited', retry_after_sec=30)
    assert e.retry_after_sec == 30


def test_rate_limited_defaults_retry_after_sec_to_5():
    from endless_library.kindle_stk.exceptions import KindleStkRateLimited
    e = KindleStkRateLimited('rate limited')
    assert e.retry_after_sec == 5


def test_all_exceptions_importable_from_package_root():
    from endless_library.kindle_stk import (
        KindleStkError,
        KindleStkNotConfigured,
        KindleStkAuthExpired,
        KindleStkRateLimited,
        KindleStkUploadFailed,
    )
    assert all(
        isinstance(e, type)
        for e in (
            KindleStkError, KindleStkNotConfigured, KindleStkAuthExpired,
            KindleStkRateLimited, KindleStkUploadFailed,
        )
    )
