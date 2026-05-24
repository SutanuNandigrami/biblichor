"""Single delivery entry-point used by the pipeline.

STK-primary + SMTP-fallback decision tree. Handles retry + backoff,
rate-gate, and event-log recording. The pipeline calls deliver(...);
it does not need to know about STK vs SMTP.

Signature adaptation notes:
- send_to_kindle() takes keyword-only args: attachment, kindle, smtp,
  title, author, max_mb_override. The plan spec passed book=/cfg=/db_path=
  which do not match. _smtp_deliver() uses the real keyword args.
- events.message is NOT NULL per schema; _record_event() requires it.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .db.schema import connect
from .kindle import send_to_kindle as _send_smtp
from .kindle_stk import (
    KindleStkAuthExpired,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkService,
    KindleStkUploadFailed,
)
from .stk_rate import quota_status as _stk_quota_status

log = logging.getLogger(__name__)


class DeliveryMethod(str, Enum):
    STK = "stk"
    SMTP = "smtp"


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    method: DeliveryMethod
    error: str | None
    attempts: int
    duration_ms: int


def deliver(
    *,
    file_path: Path,
    book: Any,
    cfg: Any,
    db_path: Path,
    svc: Any,
) -> DeliveryResult:
    """Deliver file_path to the user Kindle.

    Tries STK first if configured and quota available, with retries
    up to cfg.stk.max_attempts. Falls back to SMTP on exhaustion or
    auth failure. Records audit events on every outcome.
    """
    start = time.monotonic()
    stk_svc = KindleStkService(svc)

    # Branch 1: STK not configured -> SMTP only.
    if not stk_svc.is_configured():
        ok, err = _smtp_deliver(file_path, book, cfg, db_path=db_path)
        return DeliveryResult(
            ok=ok, method=DeliveryMethod.SMTP, error=err,
            attempts=1, duration_ms=int((time.monotonic() - start) * 1000),
        )

    # Branch 2: STK configured but daily quota exhausted -> SMTP fallback.
    status = _stk_quota_status(db_path, daily_cap=cfg.stk.daily_cap)
    if status.exhausted:
        _record_event(
            db_path, "send-stk-failed", book.id,
            message="stk-cap-reached",
            meta={"reason": "stk-cap-reached", "sent_24h": status.sent_24h, "cap": status.cap},
        )
        ok, err = _smtp_deliver(file_path, book, cfg, db_path=db_path)
        return DeliveryResult(
            ok=ok, method=DeliveryMethod.SMTP, error=err, attempts=1,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    # Branch 3: try STK up to max_attempts with retry/backoff; SMTP on exhaustion.
    max_attempts = int(cfg.stk.max_attempts)
    backoff = float(cfg.stk.backoff_initial_sec)
    factor = float(cfg.stk.backoff_factor)
    last_error: str | None = None
    auth_expired = False

    fmt = _infer_format(file_path)
    # Amazon STK's documentMetadata fields are validated as non-empty
    # strings (Member must have length >= 1). Books with NULL/empty
    # author or title cause a 400 Bad Request, which the router then
    # records as "Unexpected stkclient failure" + falls back to SMTP
    # — exactly what we don't want when STK is the configured primary.
    # Coerce to a placeholder so STK accepts.
    raw_title = getattr(book, "title", "") or ""
    raw_author = getattr(book, "author", "") or ""
    title = raw_title.strip() or file_path.stem or "Unknown title"
    author = raw_author.strip() or "Unknown"

    attempt = 1
    for attempt in range(1, max_attempts + 1):
        try:
            stk_svc.send_file(file_path, format=fmt, title=title, author=author)
            _record_event(
                db_path, "send-stk", book.id,
                message="send-stk-ok",
                meta={"attempts": attempt, "duration_ms": int((time.monotonic() - start) * 1000)},
            )
            return DeliveryResult(
                ok=True, method=DeliveryMethod.STK, error=None,
                attempts=attempt,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        except KindleStkAuthExpired as e:
            last_error = str(e)
            auth_expired = True
            log.warning("kindle_router: AuthExpired on attempt %d, skipping retries: %s", attempt, e)
            break
        except KindleStkRateLimited as e:
            last_error = str(e)
            if attempt < max_attempts:
                time.sleep(e.retry_after_sec)
        except (KindleStkUploadFailed, KindleStkNotConfigured) as e:
            last_error = str(e)
            if attempt < max_attempts:
                time.sleep(backoff)
                backoff *= factor

    _record_event(
        db_path, "send-stk-failed", book.id,
        message=last_error or "send-stk-failed",
        meta={
            "attempts": attempt, "last_error": last_error,
            "auth_expired": auth_expired, "fellback_to": "smtp",
        },
    )

    ok, err = _smtp_deliver(file_path, book, cfg, db_path=db_path)
    return DeliveryResult(
        ok=ok, method=DeliveryMethod.SMTP, error=err,
        attempts=attempt + 1,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def _smtp_deliver(
    file_path: Path,
    book: Any,
    cfg: Any,
    db_path: Path | None = None,
) -> tuple[bool, str | None]:
    """Wrap send_to_kindle() with its real keyword-only signature.

    send_to_kindle(*, attachment, kindle, smtp, title, author, max_mb_override=None)
    extracts kindle/smtp cfg blocks; if they are absent (e.g. in tests
    that monkeypatch _send_smtp entirely), the monkeypatch fires before
    this code runs so the missing attrs never matter.

    SMTP quota gate: if db_path is supplied AND cfg.smtp.daily_cap is set,
    bail out *before* the SMTP attempt when the rolling 24h count is at
    or above the cap. Prevents the router from blasting past the user's
    configured SMTP cap when STK is failing on every book and the SMTP
    fallback would otherwise hit the upstream's hard limit (and flood
    the user's Kindle inbox via the legacy email path).
    """
    if db_path is not None:
        try:
            from .smtp_rate import quota_status as _smtp_qs
            smtp_cap = int(getattr(getattr(cfg, "smtp", None), "daily_cap", 0) or 0)
            if smtp_cap > 0:
                qs = _smtp_qs(db_path, daily_cap=smtp_cap)
                if qs.exhausted:
                    log.warning(
                        "kindle_router: SMTP cap exhausted (%d/%d), skipping fallback",
                        qs.sent_24h, qs.cap,
                    )
                    return False, f"smtp-cap-reached ({qs.sent_24h}/{qs.cap})"
        except Exception as e:
            log.debug("kindle_router: smtp quota check failed (continuing): %s", e)
    try:
        kindle_cfg = getattr(cfg, "kindle", None)
        smtp_cfg = getattr(cfg, "smtp", None)
        _send_smtp(
            attachment=file_path,
            kindle=kindle_cfg,
            smtp=smtp_cfg,
            title=getattr(book, "title", str(file_path.stem)),
            author=getattr(book, "author", None),
        )
        return True, None
    except Exception as e:
        log.warning("kindle_router: SMTP fallback also failed: %s", e)
        return False, str(e)


def _infer_format(file_path: Path) -> str:
    """Return the STK format token from the file extension."""
    ext = file_path.suffix.lower().lstrip(".")
    return {
        "epub": "EPUB", "pdf": "PDF", "doc": "DOC", "docx": "DOCX",
        "txt": "TXT", "rtf": "RTF", "htm": "HTM", "html": "HTM",
        "png": "PNG", "gif": "GIF", "jpg": "JPG", "jpeg": "JPG", "bmp": "BMP",
    }.get(ext, "EPUB")


def _record_event(
    db_path: Path,
    kind: str,
    book_id: int | None,
    *,
    message: str,
    meta: dict,
) -> None:
    """Insert an audit event row. message is NOT NULL per schema."""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO events (kind, book_id, message, meta_json) VALUES (?, ?, ?, ?)",
            (kind, book_id, message, json.dumps(meta)),
        )
