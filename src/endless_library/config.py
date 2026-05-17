from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class GeneralCfg(BaseModel):
    poll_interval_minutes: int = 60
    max_attempts: int = 5
    books_dir: str = "/data/books"
    log_level: str = "INFO"
    daily_summary_hour_utc: int = 14
    timezone: str = "UTC"
    auto_pick_threshold: float = 70.0
    auto_pick_gap: float = 10.0
    min_score_for_failure: float = 40.0
    zombie_stale_minutes: int = 30


class KindleCfg(BaseModel):
    recipient: str = ""
    subject: str = "{title}"
    attachment_max_mb: int = 50


class SmtpCfg(BaseModel):
    host: str = "smtp.gmail.com"
    port: int = 587
    starttls: bool = True
    user: str = ""
    password: str = ""


class PushoverEventsCfg(BaseModel):
    book_sent: bool = True
    book_needs_review: bool = True
    cycle_summary: bool = True


class PushoverCfg(BaseModel):
    enabled: bool = False
    user_key: str = ""
    app_token: str = ""
    events: PushoverEventsCfg = PushoverEventsCfg()


class CalibreCfg(BaseModel):
    enabled: bool = True
    output_profile: str = "kindle_pw3"
    conversion_timeout_seconds: int = 300


class ScrapersCfg(BaseModel):
    order: list[str] = Field(default_factory=list)
    enabled: dict[str, bool] = Field(default_factory=dict)
    format_priority: list[str] = Field(default_factory=lambda: ["epub", "azw3", "mobi", "pdf"])
    language: str = "en"
    request_delay_seconds: float = 6.0
    slow_download_timeout_seconds: int = 180
    flaresolverr_url: str = "http://flaresolverr:8191/v1"
    annas_mirrors: list[str] = Field(default_factory=list)


class ScoringCfg(BaseModel):
    isbn_match: float = 35
    title_weight: float = 25
    author_weight: float = 15
    format_bonus: dict[str, float] = Field(default_factory=dict)
    language_bonus: float = 10
    filesize_min_bytes: int = 200_000
    filesize_max_bytes: int = 80 * 1024 * 1024
    scan_penalty: float = 10
    audio_keywords: list[str] = Field(default_factory=list)


class Config(BaseModel):
    general: GeneralCfg = GeneralCfg()
    kindle: KindleCfg = KindleCfg()
    smtp: SmtpCfg = SmtpCfg()
    pushover: PushoverCfg = PushoverCfg()
    calibre: CalibreCfg = CalibreCfg()
    scrapers: ScrapersCfg = ScrapersCfg()
    scoring: ScoringCfg = ScoringCfg()

    def public_view(self) -> dict:
        d = self.model_dump()
        if d["smtp"]["password"]:
            d["smtp"]["password"] = "***"
        if d["pushover"]["user_key"]:
            d["pushover"]["user_key"] = "***"
        if d["pushover"]["app_token"]:
            d["pushover"]["app_token"] = "***"
        return d


def _apply_env_overrides(data: dict) -> dict:
    data = deepcopy(data)
    data.setdefault("smtp", {})
    data.setdefault("kindle", {})
    data.setdefault("pushover", {})
    if v := os.getenv("SMTP_HOST"):
        data["smtp"]["host"] = v
    if v := os.getenv("SMTP_PORT"):
        data["smtp"]["port"] = int(v)
    if v := os.getenv("SMTP_USER"):
        data["smtp"]["user"] = v
    if v := os.getenv("SMTP_PASS"):
        data["smtp"]["password"] = v
    if v := os.getenv("KINDLE_EMAIL"):
        data["kindle"]["recipient"] = v
    if v := os.getenv("PUSHOVER_USER_KEY"):
        data["pushover"]["user_key"] = v
    if v := os.getenv("PUSHOVER_APP_TOKEN"):
        data["pushover"]["app_token"] = v
    return data


def load_config(path: Path | str) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw = _apply_env_overrides(raw)
    return Config.model_validate(raw)


def save_config(cfg: Config, path: Path | str) -> None:
    """Atomic write: temp file + os.rename. Strips env-injected secrets."""
    p = Path(path)
    data = cfg.model_dump()
    data["smtp"]["password"] = ""
    data["pushover"]["user_key"] = ""
    data["pushover"]["app_token"] = ""
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, p)
