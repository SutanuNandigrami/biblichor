"""Tests for Phase 6z Fix 4 (C-NEW-3): cf_bypass uses canonical assert_safe_url.

The old _is_safe_url missed: 169.254/16, 172.16/12, 100.64/10,
IPv6 link-local, .internal/.local TLDs, and DNS resolution.
"""
from __future__ import annotations

import pytest


def test_cf_bypass_rejects_aws_metadata_url():
    """169.254.169.254 (cloud metadata) must be blocked."""
    from endless_library.scrapers.cf_bypass_client import resolve
    with pytest.raises(ValueError, match="refusing to proxy"):
        resolve("http://169.254.169.254/latest/meta-data/iam/security-credentials/")


def test_cf_bypass_rejects_172_16_private():
    """172.16.0.1 (RFC1918 /12) must be blocked."""
    from endless_library.scrapers.cf_bypass_client import resolve
    with pytest.raises(ValueError, match="refusing to proxy"):
        resolve("http://172.16.0.1/admin")


def test_cf_bypass_rejects_100_64_carrier_grade_nat():
    """100.64.0.1 (carrier-grade NAT / Tailscale) must be blocked."""
    from endless_library.scrapers.cf_bypass_client import resolve
    with pytest.raises(ValueError, match="refusing to proxy"):
        resolve("http://100.64.0.1/secret")


def test_cf_bypass_rejects_internal_tld():
    """*.internal hostnames must be blocked."""
    from endless_library.scrapers.cf_bypass_client import resolve
    with pytest.raises(ValueError, match="refusing to proxy"):
        resolve("http://bookorbit-db.internal/api")


def test_cf_bypass_rejects_local_tld():
    """*.local hostnames must be blocked."""
    from endless_library.scrapers.cf_bypass_client import resolve
    with pytest.raises(ValueError, match="refusing to proxy"):
        resolve("http://printer.local/config")


def test_cf_bypass_rejects_docker_service_name():
    """Docker service names like 'bookorbit' must still be blocked."""
    from endless_library.scrapers.cf_bypass_client import resolve
    with pytest.raises(ValueError, match="refusing to proxy"):
        resolve("http://bookorbit/api/v1/users")


def test_cf_bypass_rejects_localhost():
    """localhost must be blocked."""
    from endless_library.scrapers.cf_bypass_client import resolve
    with pytest.raises(ValueError, match="refusing to proxy"):
        resolve("http://localhost:8090/api/admin")


def test_cf_bypass_rejects_ipv6_loopback():
    """::1 (IPv6 loopback) must be blocked."""
    from endless_library.scrapers.cf_bypass_client import resolve
    with pytest.raises(ValueError, match="refusing to proxy"):
        resolve("http://[::1]/admin")


def test_cf_bypass_allows_legitimate_url(monkeypatch):
    """Legitimate public URLs must pass the SSRF guard."""
    from endless_library.scrapers import cf_bypass_client

    class _R:
        status_code = 200
        text = "<html>ok</html>"
        def raise_for_status(self):
            pass

    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.httpx.get",
                        lambda *a, **kw: _R())
    monkeypatch.setenv("CF_BYPASS_URL", "http://test-bypass:8000")
    html = cf_bypass_client.resolve("https://annas-archive.gl/md5/abc123")
    assert "ok" in html
