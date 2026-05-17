from __future__ import annotations

import asyncio
import logging
import mimetypes
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

import aiosmtplib

from endless_library.config import KindleCfg, SmtpCfg

log = logging.getLogger(__name__)


class KindleSendError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SendResult:
    accepted: bool
    response: str


def build_message(
    *,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    attachment: Path,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body or "")
    ctype, _ = mimetypes.guess_type(attachment.name)
    if not ctype:
        ctype = "application/octet-stream"
    maintype, subtype = ctype.split("/", 1)
    data = attachment.read_bytes()
    msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=attachment.name)
    return msg


async def _send_smtp(
    msg: EmailMessage,
    *,
    smtp: SmtpCfg,
    timeout: float = 60.0,
) -> SendResult:
    kwargs: dict = dict(hostname=smtp.host, port=smtp.port, timeout=timeout)
    if smtp.user and smtp.password:
        kwargs["username"] = smtp.user
        kwargs["password"] = smtp.password.replace(" ", "").strip()
    if smtp.starttls:
        kwargs["start_tls"] = True
    else:
        # Implicit TLS (e.g. 465)
        if smtp.port == 465:
            kwargs["use_tls"] = True
            kwargs["tls_context"] = ssl.create_default_context()
    errors, response = await aiosmtplib.send(msg, **kwargs)
    if errors:
        raise KindleSendError(f"SMTP errors: {errors}")
    return SendResult(accepted=True, response=str(response))


def send_to_kindle(
    *,
    attachment: Path,
    kindle: KindleCfg,
    smtp: SmtpCfg,
    title: str,
    author: str | None = None,
    max_mb_override: int | None = None,
) -> SendResult:
    """Synchronous facade for the pipeline. Validates size, builds MIME, sends.

    Raises KindleSendError on any failure.
    """
    if not kindle.recipient:
        raise KindleSendError("kindle recipient not configured")
    if not smtp.host:
        raise KindleSendError("SMTP host not configured")
    size_mb = attachment.stat().st_size / (1024 * 1024)
    limit = max_mb_override or kindle.attachment_max_mb
    if size_mb > limit:
        raise KindleSendError(f"attachment {size_mb:.1f} MB exceeds limit {limit} MB")
    subject = kindle.subject.format(title=title, author=author or "")
    body = f"{title}" if not author else f"{title} — {author}"
    msg = build_message(
        sender=smtp.user,
        recipient=kindle.recipient,
        subject=subject,
        body=body,
        attachment=attachment,
    )
    return asyncio.run(_send_smtp(msg, smtp=smtp))
