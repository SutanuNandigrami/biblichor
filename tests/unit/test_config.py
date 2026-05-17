from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.config import load_config, save_config

YAML = """
general:
  poll_interval_minutes: 30
  max_attempts: 3
  books_dir: /tmp/books
  log_level: INFO
  daily_summary_hour_utc: 14
  timezone: UTC
  auto_pick_threshold: 70
  auto_pick_gap: 10
  min_score_for_failure: 40
  zombie_stale_minutes: 30
kindle: { recipient: "", subject: "{title}", attachment_max_mb: 50 }
smtp:    { host: x, port: 587, starttls: true }
pushover:
  enabled: false
  events: { book_sent: true, book_needs_review: true, cycle_summary: true }
calibre: { enabled: true, output_profile: kindle_pw3, conversion_timeout_seconds: 300 }
scrapers:
  order: [annas_curl]
  enabled: { annas_curl: true }
  format_priority: [epub]
  language: en
  request_delay_seconds: 6
  slow_download_timeout_seconds: 180
  flaresolverr_url: http://x:8191/v1
  annas_mirrors: [https://annas-archive.gl]
scoring:
  isbn_match: 35
  title_weight: 25
  author_weight: 15
  format_bonus: { epub: 10 }
  language_bonus: 10
  filesize_min_bytes: 200000
  filesize_max_bytes: 83886080
  scan_penalty: 10
  audio_keywords: [audiobook]
"""


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(YAML, encoding="utf-8")
    return p


def test_load_round_trip(cfg_path: Path) -> None:
    cfg = load_config(cfg_path)
    assert cfg.general.poll_interval_minutes == 30
    assert cfg.scrapers.order == ["annas_curl"]


def test_save_is_atomic(cfg_path: Path) -> None:
    cfg = load_config(cfg_path)
    cfg.general.poll_interval_minutes = 90
    save_config(cfg, cfg_path)
    siblings = list(cfg_path.parent.iterdir())
    assert all(not s.name.endswith(".tmp") for s in siblings)
    cfg2 = load_config(cfg_path)
    assert cfg2.general.poll_interval_minutes == 90


def test_env_overrides(cfg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_USER", "alice@example.com")
    monkeypatch.setenv("SMTP_PASS", "hunter2")
    monkeypatch.setenv("KINDLE_EMAIL", "alice@kindle.com")
    cfg = load_config(cfg_path)
    assert cfg.smtp.user == "alice@example.com"
    assert cfg.smtp.password == "hunter2"
    assert cfg.kindle.recipient == "alice@kindle.com"


def test_secret_masking(cfg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMTP_PASS", "hunter2")
    cfg = load_config(cfg_path)
    masked = cfg.public_view()
    assert masked["smtp"]["password"] == "***"
