"""Unit tests for _evict_cached_url helper.

PR follow-up to #40: when a download or unpack fails, we now ask the
scraper to evict its cached md5 -> partner URL entry so the next retry
doesn't replay the bad URL until the 1h TTL expires. The hook is
duck-typed (scrapers without invalidate_md5 are no-ops) and swallows
all exceptions (cache-flush errors must never abort the failure path).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from endless_library.pipeline import _evict_cached_url


def test_evict_calls_scraper_invalidate_md5_when_present():
    scraper = MagicMock()
    _evict_cached_url(scraper, "abc")
    scraper.invalidate_md5.assert_called_once_with("abc")


def test_evict_noop_when_scraper_has_no_invalidate_method():
    class _BareScraper:
        pass

    scraper = _BareScraper()
    # Must not raise even though there's no invalidate_md5 attribute.
    _evict_cached_url(scraper, "abc")


def test_evict_noop_on_empty_md5():
    scraper = MagicMock()
    _evict_cached_url(scraper, None)
    _evict_cached_url(scraper, "")
    scraper.invalidate_md5.assert_not_called()


def test_evict_swallows_invalidate_errors():
    scraper = MagicMock()
    scraper.invalidate_md5.side_effect = RuntimeError("disk full")
    # Must not raise -- pipeline failure paths can't be aborted by a
    # cache-flush IOError.
    _evict_cached_url(scraper, "abc")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
