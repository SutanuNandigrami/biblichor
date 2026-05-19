"""Phase 6o.4 tests for the dynamic /api/settings BookOrbit URL surface."""

from __future__ import annotations

from types import SimpleNamespace

from endless_library.web.api import _compute_bookorbit_urls


def _fake_request(scheme: str, host: str, port: int | None = None):
    """Build a minimal request-like object with .url.scheme + .url.hostname."""
    url = SimpleNamespace(scheme=scheme, hostname=host, port=port)
    return SimpleNamespace(url=url)


def _cfg(url: str):
    return SimpleNamespace(bookorbit=SimpleNamespace(url=url))


# ============ REGRESSION: D-1 — never use localhost when SPA was loaded from elsewhere ============


def test_localhost_in_cfg_url_is_overridden_by_request_host():
    """The exact bug the docs warn about: APP_URL=http://localhost:3000 in
    config but the user loaded biblichor at http://claude-1:8090 in their
    browser. The Library page must link to claude-1, not localhost."""
    req = _fake_request("http", "claude-1")
    urls = _compute_bookorbit_urls(req, _cfg("http://localhost:3000"))
    assert urls["dashboard"] == "http://claude-1:3000"
    assert "localhost" not in urls["opds_catalog"]
    assert "claude-1" in urls["kobo_sync_root"]


def test_127001_in_cfg_url_also_overridden():
    """127.0.0.1 has the same cross-device problem as 'localhost'."""
    req = _fake_request("http", "biblichor.tailnet.ts")
    urls = _compute_bookorbit_urls(req, _cfg("http://127.0.0.1:3000"))
    assert urls["dashboard"] == "http://biblichor.tailnet.ts:3000"


def test_cfg_url_does_not_leak_into_spa_urls():
    """Phase 6o.10: cfg.bookorbit.url is the INTERNAL API URL only.
    Even a perfectly-resolvable configured URL must NOT appear in
    the SPA URLs — those are always request-derived (or set via
    BOOKORBIT_EXTERNAL_URL). This prevents the container hostname
    (e.g. http://bookorbit:3000) from ever being shown to the browser."""
    req = _fake_request("http", "claude-1")
    urls = _compute_bookorbit_urls(req, _cfg("http://bookorbit:3000"))
    # The internal docker hostname must NEVER appear in user-facing URLs
    assert "bookorbit:3000" not in urls["dashboard"]
    assert urls["dashboard"] == "http://claude-1:3000"


def test_bookorbit_external_url_env_overrides_request_host(monkeypatch):
    """Phase 6o.10: For reverse-proxy setups where BookOrbit is published
    at a different hostname than biblichor, BOOKORBIT_EXTERNAL_URL is the
    explicit escape hatch."""
    monkeypatch.setenv("BOOKORBIT_EXTERNAL_URL", "https://books.example.com")
    req = _fake_request("http", "biblichor.example.com")
    urls = _compute_bookorbit_urls(req, _cfg("http://bookorbit:3000"))
    assert urls["dashboard"] == "https://books.example.com"
    assert urls["opds_catalog"] == "https://books.example.com/api/v1/opds"


def test_bookorbit_external_url_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("BOOKORBIT_EXTERNAL_URL", "https://books.example.com/")
    req = _fake_request("http", "host")
    urls = _compute_bookorbit_urls(req, _cfg(""))
    assert urls["dashboard"] == "https://books.example.com"
    assert "//api" not in urls["opds_catalog"]


def test_empty_cfg_url_falls_back_to_request_host():
    req = _fake_request("https", "books.tailnet.ts")
    urls = _compute_bookorbit_urls(req, _cfg(""))
    assert urls["dashboard"] == "https://books.tailnet.ts:3000"


# ============ FEATURE-INTACT: URL shape contract ============


def test_all_documented_urls_present():
    """The SPA depends on these exact keys. Pin them."""
    req = _fake_request("http", "host")
    urls = _compute_bookorbit_urls(req, _cfg(""))
    EXPECTED = {
        "dashboard",
        "opds_catalog",
        "kobo_sync_root",
        "koreader_sync",
        "statistics",
        "reader_base",
        "base",
    }
    assert set(urls.keys()) >= EXPECTED


def test_opds_catalog_uses_documented_api_v1_path():
    """Per BookOrbit's opds.html — the catalog is at /api/v1/opds."""
    req = _fake_request("http", "host")
    urls = _compute_bookorbit_urls(req, _cfg(""))
    assert urls["opds_catalog"].endswith("/api/v1/opds")


def test_kobo_sync_root_uses_documented_path():
    """Per BookOrbit's kobo.html — per-device sync at /api/v1/kobo/{token}."""
    req = _fake_request("http", "host")
    urls = _compute_bookorbit_urls(req, _cfg(""))
    assert urls["kobo_sync_root"].endswith("/api/v1/kobo")


def test_statistics_path():
    req = _fake_request("http", "host")
    urls = _compute_bookorbit_urls(req, _cfg(""))
    assert urls["statistics"].endswith("/statistics")


def test_port_override_via_env(monkeypatch):
    """If BOOKORBIT_PORT is set, fallback URLs use it."""
    monkeypatch.setenv("BOOKORBIT_PORT", "3050")
    req = _fake_request("http", "host")
    urls = _compute_bookorbit_urls(req, _cfg(""))
    assert urls["dashboard"] == "http://host:3050"


def test_https_scheme_preserved():
    """If the user loads biblichor via HTTPS (e.g. behind Caddy/Tailscale),
    derived BookOrbit URLs also use HTTPS."""
    req = _fake_request("https", "books.example.com")
    urls = _compute_bookorbit_urls(req, _cfg(""))
    assert urls["dashboard"].startswith("https://")
