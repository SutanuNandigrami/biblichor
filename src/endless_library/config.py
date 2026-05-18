from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class GeneralCfg(BaseModel):
    poll_interval_minutes: int = 60
    process_interval_minutes: int = 10
    retry_interval_hours: int = 6
    mirror_refresh_hours: int = 6
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
    # When BOTH query and candidate title contain non-Latin glyphs we
    # extract the non-Latin substrings before fuzz-matching (handles
    # transliterated candidates like 'Kantai Kantai 6 (কাঁটায় কাঁটায়-৬)')
    # and multiply the resulting title_similarity component by this
    # value. Default 1.6 — empirically lets a near-perfect Bengali/
    # Devanagari/CJK title carry past the 70-pt auto-pick threshold
    # with a normal format+language+filesize bonus stack.
    non_latin_title_multiplier: float = 2.0


class SecurityCfg(BaseModel):
    """Hygiene + AV scanning of downloaded archives.

    Defaults are pragmatic for first-run (ClamAV optional, allow archives
    after hygiene passes). Flip require_clamav: true once you've installed
    `clamav` to enforce signature scanning.
    """

    require_clamav: bool = False
    max_archive_size_mb: int = 200
    max_extracted_size_mb: int = 500
    max_members: int = 50


class Config(BaseModel):
    general: GeneralCfg = GeneralCfg()
    kindle: KindleCfg = KindleCfg()
    smtp: SmtpCfg = SmtpCfg()
    pushover: PushoverCfg = PushoverCfg()
    calibre: CalibreCfg = CalibreCfg()
    scrapers: ScrapersCfg = ScrapersCfg()
    scoring: ScoringCfg = ScoringCfg()
    security: SecurityCfg = SecurityCfg()

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


_ENV_KEYS = (
    ("SMTP_HOST", "smtp", "host"),
    ("SMTP_PORT", "smtp", "port"),
    ("SMTP_USER", "smtp", "user"),
    ("SMTP_PASS", "smtp", "password"),
    ("KINDLE_EMAIL", "kindle", "recipient"),
    ("PUSHOVER_USER_KEY", "pushover", "user_key"),
    ("PUSHOVER_APP_TOKEN", "pushover", "app_token"),
)


def _default_env_path(yaml_path: Path) -> Path:
    """Return the .env path that sits next to the yaml config."""
    return Path(yaml_path).parent / ".env"


def load_config(path: Path | str, *, env_path: Path | str | None = None) -> Config:
    """Read yaml, then pull SMTP/Pushover/Kindle secrets out of the .env next to it
    (or the one passed explicitly). Real process env vars override .env."""
    path = Path(path)
    if env_path is None:
        env_path = _default_env_path(path)
    env_path = Path(env_path)
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            # Use the .env value when process env var is unset OR empty;
            # treat empty-string env vars as not-really-set.
            if not os.environ.get(k) and v:
                os.environ[k] = v
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = _apply_env_overrides(raw)
    return Config.model_validate(raw)


def _write_env(env_path: Path, cfg: Config) -> None:
    """Persist secrets to .env without disturbing other keys."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    for env_key, section, attr in _ENV_KEYS:
        value = getattr(getattr(cfg, section), attr)
        existing[env_key] = "" if value is None else str(value)
    tmp = env_path.with_suffix(env_path.suffix + ".tmp")
    tmp.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, env_path)


def save_config(cfg: Config, path: Path | str, *, env_path: Path | str | None = None) -> None:
    """Atomic write: yaml gets non-secrets, .env gets secrets. Both atomic-rename."""
    p = Path(path)
    if env_path is None:
        env_path = _default_env_path(p)
    env_path = Path(env_path)
    data = cfg.model_dump()
    # Strip secrets from yaml — they live in .env
    data["smtp"]["password"] = ""
    data["pushover"]["user_key"] = ""
    data["pushover"]["app_token"] = ""
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, p)
    _write_env(env_path, cfg)
