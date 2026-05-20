"""Phase 6s.6 tests — Tor sidecar in compose + opt-in flag."""

from __future__ import annotations

from pathlib import Path


def test_compose_has_optional_torproxy_service():
    import yaml as pyyaml

    compose_path = Path(__file__).parent.parent.parent / "deploy" / "compose.yml"
    compose = pyyaml.safe_load(compose_path.read_text())
    services = compose.get("services", {})
    assert "tor" in services, "torproxy service must be in compose.yml"
    # And it must be profile-gated (off by default)
    assert "tor" in services["tor"].get("profiles", []), (
        "tor service must be behind --profile tor (off by default)"
    )


def test_scrapers_cfg_has_tor_fields():
    from endless_library.config import ScrapersCfg

    cfg = ScrapersCfg()
    assert hasattr(cfg, "tor_enabled")
    assert cfg.tor_enabled is False  # default off
    assert hasattr(cfg, "tor_proxy_url")
    assert cfg.tor_proxy_url.startswith("socks5h://")
