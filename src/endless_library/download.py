from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from endless_library.domain.models import DownloadHandle
from endless_library.scrapers.base import BOOK_EXTENSIONS

log = logging.getLogger(__name__)

CHUNK = 64 * 1024


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    size: int
    md5: str
    content_type: str | None


class DownloadError(Exception):
    pass


def _filename_from_handle(handle: DownloadHandle, fallback: str) -> str:
    if handle.expected_filename:
        return handle.expected_filename
    # Pull last URL segment with a book ext, else fallback
    tail = handle.url.split("?", 1)[0].rsplit("/", 1)[-1]
    if "." in tail and tail.rsplit(".", 1)[-1].lower() in BOOK_EXTENSIONS:
        return tail
    return fallback


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._\- ]+", "_", name).strip()
    return name[:200] or "book"


def download(
    handle: DownloadHandle,
    *,
    dest_dir: Path,
    fallback_name: str,
    expected_md5: str | None = None,
    client_factory=None,  # for tests
) -> DownloadResult:
    """Stream the URL to `<safe>.part`, atomic-rename on success. Verify MD5 if provided."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(_filename_from_handle(handle, fallback_name))
    final = dest_dir / name
    part = final.with_suffix(final.suffix + ".part")

    factory = client_factory or (
        lambda: httpx.Client(timeout=60.0, follow_redirects=True, headers=handle.headers)
    )
    md5 = hashlib.md5()
    total = 0
    content_type: str | None = None
    with factory() as client, client.stream("GET", handle.url) as r:
        if r.status_code != 200:
            raise DownloadError(f"HTTP {r.status_code} from {handle.url}")
        content_type = r.headers.get("content-type")
        if content_type and "text/html" in content_type.lower():
            snippet = r.read()[:200]
            raise DownloadError(f"got HTML, not book (ct={content_type}): {snippet!r}")
        with part.open("wb") as f:
            for chunk in r.iter_bytes(CHUNK):
                if not chunk:
                    continue
                f.write(chunk)
                md5.update(chunk)
                total += len(chunk)
    digest = md5.hexdigest()
    if expected_md5 and digest != expected_md5:
        part.unlink(missing_ok=True)
        raise DownloadError(f"md5 mismatch: got {digest}, expected {expected_md5}")
    part.replace(final)
    return DownloadResult(path=final, size=total, md5=digest, content_type=content_type)
