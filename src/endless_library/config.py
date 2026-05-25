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
    retention_hour_utc: int = 3  # daily DB prune (events + bench history)
    retention_keep_events: int = 50_000  # max rows to keep in `events`
    retention_keep_events_days: int = 90  # OR drop older than this, whichever cuts more
    retention_keep_bench_per_scraper: int = 200
    max_attempts: int = 5
    books_dir: str = "/data/books"
    log_level: str = "INFO"
    daily_summary_hour_utc: int = 14
    timezone: str = "UTC"
    auto_pick_threshold: float = 70.0
    # Bengali/Devanagari/CJK queries without ISBN top out empirically
    # at score 65-70 because rapidfuzz can't ride a 10x boost on already-
    # high token overlap. A separate, lower auto-pick threshold lets
    # non-Latin titles auto-pick at score 45 (the empirical confident-
    # match floor on the existing queue).
    auto_pick_threshold_non_latin: float = 45.0
    # Quality floor for scraper-chain fallthrough. If the BEST candidate from
    # the current scraper is below this floor, keep going to the next scraper
    # rather than stopping at the first one to return ANY results.
    # Latin queries with an ISBN can clear 60 easily (ISBN match alone is 35).
    # Non-Latin queries clear 40 only when the title actually matches.
    fallthrough_quality_floor: float = 60.0
    # Phase 3c: after this many search rounds that yield no viable
    # candidate (no scrapers returned, all hard-skipped, or top score
    # below min_score_for_failure), park the book in `skipped` rather
    # than keep cycling it through `failed`. 3 = generous default.
    max_search_attempts_before_skip: int = 3
    fallthrough_quality_floor_non_latin: float = 40.0
    auto_pick_gap: float = 10.0
    min_score_for_failure: float = 40.0
    # Lower failure floor for non-Latin queries. rapidfuzz on
    # Bengali/Devanagari/CJK rarely clears 40 without ISBN; keep the
    # near-misses as needs_review rather than dropping them.
    min_score_for_failure_non_latin: float = 25.0
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
    # SEMANTICS: encoded MIME size cap in MB, NOT raw attachment size.
    # The pipeline rejects when `raw_bytes * 1.4 > max_attachment_mb`
    # (1.4 = base64's 4/3 inflation + header/boundary overhead). So
    # the effective raw ceiling is `max_attachment_mb / 1.4`.
    #
    # Gmail outbound is capped at 25 MB MIME-encoded -> we set 24 to
    # keep a 1 MB safety margin. That yields a raw ceiling of 17.1 MB,
    # which fits typical EPUBs/MOBIs and roughly 95%% of the queue.
    # The previous default of 22 yielded only 15.7 MB raw - too tight.
    # NOTE: Gmail's 50 MB number applies to *inbound*; outbound is 25.
    # Other providers: SES 40 MB, SendGrid 30 MB. Kindle inbound: 50.
    max_attachment_mb: int = 24
    # Phase 6u: rolling-24h send cap. Gmail free ≈ 100/day, Workspace
    # ≈ 2000/day. We default to 80 to leave headroom for non-Kindle
    # outbound (test emails, doctor probes, etc). 0 disables the gate.
    # When the cap is hit, the pipeline defers the send to the next
    # cycle instead of marking the book failed — books self-pace into
    # the SMTP budget over multiple cycles instead of all dying
    # together when one big source backfill hits the wall.
    daily_cap: int = 80


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


class BdebooksCfg(BaseModel):
    excluded_categories: list[str] = Field(default_factory=lambda: [
        "Islamic Books",
        "ইসলামিক বই",
        "Islamic",
        "Islam",
        "Religion",
        "Religious",
        "ধর্ম",
        "ধর্মীয়",
        "Hadith",
        "হাদিস",
        "Quran",
        "কোরআন",
        "Prophet",
        "নবী",
        "Islamic Studies",
        "ইসলামিক স্টাডিজ",
    ])


class KindleBanglaCfg(BaseModel):
    excluded_categories: list[str] = Field(default_factory=lambda: [
        "Islamic",
        "Religion",
        "Religious",
        "ধর্মীয়",
        "Hadith",
        "Quran",
    ])


class ScrapersCfg(BaseModel):
    order: list[str] = Field(default_factory=list)
    enabled: dict[str, bool] = Field(default_factory=dict)
    format_priority: list[str] = Field(default_factory=lambda: ["epub", "azw3", "mobi", "pdf"])
    language: str = "en"
    request_delay_seconds: float = 6.0
    slow_download_timeout_seconds: int = 180
    flaresolverr_url: str = "http://flaresolverr:8191/v1"
    # Phase 6s.6: route Anna's onion fallback through the optional
    # torproxy sidecar at tor:9050. Off by default; opt in via
    # `docker compose --profile tor up -d` + setting this flag.
    tor_enabled: bool = False
    tor_proxy_url: str = "socks5h://tor:9050"
    annas_mirrors: list[str] = Field(default_factory=list)
    # Optional Welib auth cookie. Paste as the literal Cookie: header value
    # the browser would send after logging into welib.org
    # (e.g. 'session_id=abc; user_id=42'). Stored as a secret in .env, never
    # committed to config.yaml. Injected into both FlareSolverr and Playwright
    # so /fast_download/ works without the slow-download countdown.
    welib_auth_cookie: str | None = None
    # Phase 6w.5: books published within this many years of today are
    # considered "recent releases" and get mobilism_books promoted to
    # the front of the scraper chain.
    recent_release_window_years: int = 1
    # Phase 6w.5: Mobilism forum credentials (stored via secrets store;
    # these fields are populated from the encrypted DB at runtime by
    # the scraper, not from config.yaml directly).
    mobilism_username: str = ""
    mobilism_password: str = ""
    bdebooks: BdebooksCfg = Field(default_factory=BdebooksCfg)
    kindlebangla: KindleBanglaCfg = Field(default_factory=KindleBanglaCfg)


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
    # value. Default 2.0 — empirically lets a near-perfect Bengali/
    # Devanagari/CJK title carry past the (lower) non-Latin auto-pick
    # threshold of 45 with a normal format+language+filesize bonus stack.
    # With multiplier=2.0 + ISBN match + author match, scores can
    # exceed 100 raw — that's fine, the score is monotonic for the
    # picker, not a percentage.
    non_latin_title_multiplier: float = 2.0
    # If set, candidates whose filesize_bytes exceeds this hard-skip
    # with reason='oversize'. Pipeline sets this dynamically from
    # cfg.smtp.max_attachment_mb so the picker naturally avoids
    # candidates that couldn't be delivered as-is anyway.
    deliverable_max_bytes: int | None = None


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


class BookOrbitCfg(BaseModel):
    """BookOrbit integration (Phase 6).

    BookOrbit is biblichor's library frontend — it owns reader, Kobo
    sync, KOReader two-way, OPDS, statistics. biblichor stays the
    acquisition pipeline. After every successful Kindle send, the
    pipeline copies the enriched file into BookOrbit's watched
    library directory; @parcel/watcher ingests automatically.

    Default disabled so back-compat preserved. Flip enabled=true
    after running `biblichor bookorbit-setup` (which writes the
    fields below directly into config.yaml — Phase 6m.ii made this
    the single source of truth; an earlier design used a separate
    config/bookorbit.json that no longer exists).
    """

    enabled: bool = False
    # External URL biblichor's SPA links to. Falls back to
    # http://localhost:3000 if not set. Override via BOOKORBIT_URL.
    url: str = ""
    # Path biblichor SEES the BookOrbit-watched library at. In a docker
    # compose deployment this is the container-internal mount point
    # (default: /library). In a native deployment this is the host
    # path biblichor was launched from. Pre-Phase 6o.2 this field
    # was named library_root_on_host — the rename clarifies that the
    # value isn't always a host path. Setup writes the right value
    # depending on how it's invoked.
    library_root: str = ""
    # "book_per_folder" (default in BookOrbit) -> <author>/<title>/<file>
    # "book_per_file" -> flat
    organization_mode: str = "book_per_folder"
    # The BookOrbit library this biblichor instance owns. Populated by
    # `biblichor bookorbit-setup`. Used by `migrate-to-bookorbit
    # --trigger-scan`; pipeline drop doesn't need it (the watcher
    # auto-detects). Phase 6m.ii made this the single source of truth
    # (was previously duplicated in config/bookorbit.json).
    library_id: str = ""


class StorageCfg(BaseModel):
    """Pluggable blob storage. Default is `local`, which behaves
    exactly like pre-Phase-4 biblichor.

    backend = local | rclone | hybrid
      local   - files live under local_root (defaults to general.books_dir).
      rclone  - all I/O goes to an rclone remote (gdrive / s3 / hetzner / ...).
      hybrid  - primary=local, backup=rclone, with hybrid_mode controlling
                when the backup write happens (mirror=sync, scheduled=cron).

    Rclone setup is one-time:
      $ rclone config            # interactive, configures remote auth
    Then point rclone_remote at the name from rclone.conf.
    """

    backend: str = "local"
    # local backend
    local_root: str = ""  # empty = use general.books_dir
    # rclone / hybrid backend
    rclone_remote: str = ""  # name from rclone.conf, e.g. "gdrive"
    rclone_bucket_path: str = ""  # optional path prefix on the remote
    # hybrid only
    hybrid_mode: str = "mirror"  # "mirror" or "scheduled"




class BenchCfg(BaseModel):
    per_query_timeout_sec: int = 20
    circuit_break_after_consecutive_fails: int = 3


class StkCfg(BaseModel):
    """Send-to-Kindle delivery configuration."""
    daily_cap: int | None = None
    max_attempts: int = 3
    backoff_initial_sec: float = 5.0
    backoff_factor: float = 3.0
    client_id: str | None = None  # None = use vendored stkclient's hardcoded value
    amazon_domain: str = "amazon.com"  # Override for regional TLDs (.in, .co.uk, etc.)
    # Phase STK-recovery: minimum gap between consecutive STK sends (seconds).
    # Prevents hitting Amazon anti-abuse threshold (~420 rapid sends/hour
    # revoked our device cert). Default 5.0 s => at most 720 sends/hour,
    # well below the observed revocation threshold.
    min_send_interval_sec: float = 10.0
    # STK multi-file batching: max number of files to pack into one STK
    # session (one GetUploadUrl + upload + SendToKindle cycle per file,
    # but all within the same signed session and one inter-batch sleep).
    # Hard budget is 200 MB total per batch. This is a defensive upper
    # bound so 500 tiny books don't collapse into a single giant call.
    max_batch_files: int = 25
    # Total byte budget for a single STK batch (Amazon 200 MB cap).
    max_batch_bytes: int = 200 * 1024 * 1024


class Config(BaseModel):
    general: GeneralCfg = GeneralCfg()
    kindle: KindleCfg = KindleCfg()
    smtp: SmtpCfg = SmtpCfg()
    pushover: PushoverCfg = PushoverCfg()
    calibre: CalibreCfg = CalibreCfg()
    scrapers: ScrapersCfg = ScrapersCfg()
    scoring: ScoringCfg = ScoringCfg()
    security: SecurityCfg = SecurityCfg()
    storage: StorageCfg = StorageCfg()
    bookorbit: BookOrbitCfg = BookOrbitCfg()
    bench: BenchCfg = BenchCfg()
    stk: StkCfg = StkCfg()

    def public_view(self) -> dict:
        d = self.model_dump()
        if d["smtp"]["password"]:
            d["smtp"]["password"] = "***"
        if d["pushover"]["user_key"]:
            d["pushover"]["user_key"] = "***"
        if d["pushover"]["app_token"]:
            d["pushover"]["app_token"] = "***"
        if d.get("scrapers", {}).get("welib_auth_cookie"):
            d["scrapers"]["welib_auth_cookie"] = "***"
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
    if v := os.getenv("WELIB_AUTH_COOKIE"):
        data.setdefault("scrapers", {})["welib_auth_cookie"] = v
    if v := os.getenv("BOOKORBIT_URL"):
        data.setdefault("bookorbit", {})["url"] = v
    if v := os.getenv("FLARESOLVERR_URL"):
        data.setdefault("scrapers", {})["flaresolverr_url"] = v
    return data


_ENV_KEYS = (
    ("SMTP_HOST", "smtp", "host"),
    ("SMTP_PORT", "smtp", "port"),
    ("SMTP_USER", "smtp", "user"),
    ("SMTP_PASS", "smtp", "password"),
    ("KINDLE_EMAIL", "kindle", "recipient"),
    ("PUSHOVER_USER_KEY", "pushover", "user_key"),
    ("PUSHOVER_APP_TOKEN", "pushover", "app_token"),
    ("WELIB_AUTH_COOKIE", "scrapers", "welib_auth_cookie"),
    ("BOOKORBIT_URL", "bookorbit", "url"),
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
    data.setdefault("scrapers", {})["welib_auth_cookie"] = None
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, p)
    _write_env(env_path, cfg)
