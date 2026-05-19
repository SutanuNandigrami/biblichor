"""Tests for Phase 6j: BookOrbit URL surfaced via config + /api/settings."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from endless_library.config import BookOrbitCfg, Config, load_config, save_config


@pytest.fixture(autouse=True)
def _clean_bookorbit_env(monkeypatch):
    """load_config seeds process env from .env (intentional in
    production, but causes test cross-pollination). Wipe BOOKORBIT_URL
    before every test in this module."""
    monkeypatch.delenv("BOOKORBIT_URL", raising=False)


# ============ Config: url field + BOOKORBIT_URL env override ============


def test_bookorbit_cfg_has_url_field_default_empty():
    """Default empty means SPA uses runtime fallback to :3000."""
    c = BookOrbitCfg()
    assert c.url == ""


def test_bookorbit_url_env_override(tmp_path, monkeypatch):
    """BOOKORBIT_URL env variable wins over config.yaml (matches the
    pattern for SMTP/KINDLE/etc)."""
    cfg_path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.bookorbit.url = "http://config-yaml-set.local:3000"
    save_config(cfg, cfg_path)

    monkeypatch.setenv("BOOKORBIT_URL", "http://env-wins.local:3050")
    loaded = load_config(cfg_path)
    assert loaded.bookorbit.url == "http://env-wins.local:3050"


def test_bookorbit_url_in_public_view():
    """The /api/settings endpoint serves cfg.public_view(); url must be
    in there so the SPA can read it."""
    c = Config()
    c.bookorbit.url = "http://x:3050"
    pub = c.public_view()
    assert "bookorbit" in pub
    assert pub["bookorbit"]["url"] == "http://x:3050"


# ============ Config persistence: setup keeps url across saves ============


def test_setup_preserves_url_when_set(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg = Config()
    cfg.bookorbit.url = "http://my-tailscale-name:3000"
    save_config(cfg, cfg_path)
    reloaded = load_config(cfg_path)
    assert reloaded.bookorbit.url == "http://my-tailscale-name:3000"
