"""Tests for welib_cookies parsing + injection paths."""

from __future__ import annotations

from endless_library.scrapers.welib_cookies import (
    parse_cookie_string,
    to_header_value,
    to_playwright,
)


def test_parse_empty():
    assert parse_cookie_string("") == []
    assert parse_cookie_string(None) == []


def test_parse_single_cookie():
    out = parse_cookie_string("session_id=abc123")
    assert out == [{"name": "session_id", "value": "abc123", "domain": "welib.org", "path": "/"}]


def test_parse_multiple_semicolon_separated():
    out = parse_cookie_string("a=1; b=2; c=3")
    assert [c["name"] for c in out] == ["a", "b", "c"]
    assert [c["value"] for c in out] == ["1", "2", "3"]


def test_parse_newline_separated():
    out = parse_cookie_string("a=1\nb=2\nc=3")
    assert [c["name"] for c in out] == ["a", "b", "c"]


def test_parse_strips_whitespace():
    out = parse_cookie_string("  a = 1 ;   b=2 ")
    assert out[0] == {"name": "a", "value": "1", "domain": "welib.org", "path": "/"}
    assert out[1] == {"name": "b", "value": "2", "domain": "welib.org", "path": "/"}


def test_parse_skips_malformed():
    # No '=' → skip; empty name → skip; empty value → skip
    out = parse_cookie_string("garbage; =onlyvalue; name_only=; valid=ok")
    assert [c["name"] for c in out] == ["valid"]


def test_parse_custom_domain():
    out = parse_cookie_string("k=v", domain="example.com")
    assert out[0]["domain"] == "example.com"


def test_to_header_value_roundtrip():
    raw = "a=1; b=2; c=hello"
    parsed = parse_cookie_string(raw)
    rebuilt = to_header_value(parsed)
    # Re-parsing the rebuilt string yields the same data
    assert parse_cookie_string(rebuilt) == parsed


def test_to_playwright_is_identity():
    parsed = parse_cookie_string("a=1; b=2")
    assert to_playwright(parsed) == parsed


# ============ end-to-end: config -> parse ============


def test_config_loads_cookie_from_env(tmp_path, monkeypatch):
    """The welib_auth_cookie field should be populated from WELIB_AUTH_COOKIE
    environment variable like the other secrets."""
    from endless_library.config import load_config

    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("general: {}\nsmtp: {}\n")
    env_path = tmp_path / ".env"
    env_path.write_text("WELIB_AUTH_COOKIE=session_id=fromenv\n")
    # Make sure the live process env doesn't clobber the .env value
    monkeypatch.delenv("WELIB_AUTH_COOKIE", raising=False)
    cfg = load_config(yaml_path, env_path=env_path)
    assert cfg.scrapers.welib_auth_cookie == "session_id=fromenv"


def test_config_save_strips_cookie_from_yaml(tmp_path, monkeypatch):
    """save_config must keep welib_auth_cookie OUT of yaml; only .env carries it."""
    from endless_library.config import Config, save_config

    cfg = Config()
    cfg.scrapers.welib_auth_cookie = "session_id=secret"
    yaml_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    monkeypatch.setenv("HOME", str(tmp_path))
    save_config(cfg, yaml_path, env_path=env_path)
    yaml_text = yaml_path.read_text()
    env_text = env_path.read_text()
    assert "session_id=secret" not in yaml_text
    assert "session_id=secret" in env_text


def test_public_view_masks_cookie():
    from endless_library.config import Config

    cfg = Config()
    cfg.scrapers.welib_auth_cookie = "session_id=secretvalue"
    view = cfg.public_view()
    assert view["scrapers"]["welib_auth_cookie"] == "***"


def test_public_view_none_when_unset():
    from endless_library.config import Config

    cfg = Config()
    view = cfg.public_view()
    # Unset cookies stay as None in the view (no "***")
    assert view["scrapers"]["welib_auth_cookie"] is None
