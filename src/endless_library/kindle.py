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
    """Send via aiosmtplib and translate every conceivable SMTP failure
    into a KindleSendError the pipeline can catch cleanly.

    Without this translation, aiosmtplib's SMTPException subclasses
    (SMTPDataError on Gmail's 552 size limit, SMTPAuthenticationError,
    SMTPRecipientsRefused, etc.) bubbled past `send_to_kindle`'s caller
    and crashed the whole pipeline run with a raw tuple in the traceback.
    """
    kwargs: dict = dict(hostname=smtp.host, port=smtp.port, timeout=timeout)
    if smtp.user and smtp.password:
        kwargs["username"] = smtp.user
        kwargs["password"] = smtp.password.replace(" ", "").strip()
    if smtp.starttls:
        kwargs["start_tls"] = True
    elif smtp.port == 465:
        # Implicit TLS
        kwargs["use_tls"] = True
        kwargs["tls_context"] = ssl.create_default_context()
    try:
        errors, response = await aiosmtplib.send(msg, **kwargs)
    except aiosmtplib.SMTPAuthenticationError as e:
        raise KindleSendError(
            f"SMTP authentication failed (check SMTP_USER / SMTP_PASS in .env, "
            f"and that app-passwords aren't expired): {e}"
        ) from e
    except aiosmtplib.SMTPRecipientsRefused as e:
        raise KindleSendError(
            f"SMTP recipient rejected. Add SMTP_USER to your Amazon "
            f'"Approved Personal Document E-mail List" at '
            f"amazon.com/hz/mycd/myx#/home/settings/payment: {e}"
        ) from e
    except aiosmtplib.SMTPDataError as e:
        # Gmail returns 552 for oversize messages (>25 MB outbound). Other
        # 5xx data errors usually mean spam-rejected. Surface code clearly.
        code = getattr(e, "code", None)
        if code == 552:
            raise KindleSendError(
                f"SMTP rejected: message too large for the SMTP server "
                f"(Gmail caps outbound at ~25 MB). Lower smtp.max_attachment_mb "
                f"or switch SMTP provider: {e}"
            ) from e
        raise KindleSendError(f"SMTP data error (code={code}): {e}") from e
    except aiosmtplib.SMTPConnectError as e:
        raise KindleSendError(
            f"Could not connect to SMTP server {smtp.host}:{smtp.port}: {e}"
        ) from e
    except aiosmtplib.SMTPTimeoutError as e:
        raise KindleSendError(f"SMTP timed out after {timeout}s sending to {smtp.host}: {e}") from e
    except aiosmtplib.SMTPServerDisconnected as e:
        raise KindleSendError(f"SMTP server disconnected mid-send: {e}") from e
    except aiosmtplib.SMTPException as e:
        # Catch-all for any other aiosmtplib subclass we didn\'t enumerate
        raise KindleSendError(f"SMTP error: {e}") from e

    if errors:
        # aiosmtplib also surfaces non-fatal errors via the `errors` dict
        # (e.g. when one recipient is refused but another succeeded).
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
