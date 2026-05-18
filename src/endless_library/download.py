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
    # Pull last URL segment with a book ext, else fallback. URL-decode first so
    # %20 etc. become spaces, then strip Anna's boilerplate before safe_filename
    # has to truncate.
    from urllib.parse import unquote

    tail = unquote(handle.url.split("?", 1)[0].rsplit("/", 1)[-1])
    if "." in tail and tail.rsplit(".", 1)[-1].lower() in BOOK_EXTENSIONS:
        return clean_book_filename(tail)
    return fallback


_ANNAS_TRAIL_RE = re.compile(
    r"\s+--\s+(?:Anna[\u2019']s\s+Archive)\s*$",
    re.IGNORECASE,
)
_MD5_SEGMENT_RE = re.compile(r"\s+--\s+[0-9a-f]{32}\b", re.IGNORECASE)


def clean_book_filename(name: str) -> str:
    """Strip Anna's Archive boilerplate from a CDN filename.

    Examples:
      "Title -- Author -- 2017 -- Pub -- abc...32hex -- Anna's Archive.epub"
        -> "Title -- Author -- 2017 -- Pub.epub"
      "Some File.epub" -> "Some File.epub" (unchanged when no markers)
    """
    if "." not in name:
        return name
    stem, _, ext = name.rpartition(".")
    # 1. Strip trailing "-- Anna's Archive"
    stem = _ANNAS_TRAIL_RE.sub("", stem)
    # 2. Strip the standalone " -- <32hex>" md5 segment anywhere in the stem
    stem = _MD5_SEGMENT_RE.sub("", stem)
    return (stem.strip() + "." + ext) if stem.strip() else name


def safe_filename(name: str, *, max_length: int = 200) -> str:
    """Sanitize a filename; preserve the extension when truncating."""
    name = re.sub(r"[^A-Za-z0-9._\- ]+", "_", name).strip()
    if not name:
        return "book"
    if len(name) <= max_length:
        return name
    # Preserve extension if present (e.g., .epub, .pdf, .azw3)
    stem, dot, ext = name.rpartition(".")
    if dot and 1 <= len(ext) <= 6 and ext.isalnum():
        keep_for_stem = max_length - len(ext) - 1
        return stem[:keep_for_stem].rstrip(" _-.") + "." + ext
    return name[:max_length]


_MAX_REDIRECTS = 5


def _walk_redirects(client: httpx.Client, url: str) -> str:
    """Walk a redirect chain manually, asserting each hop's URL is safe.

    Returns the final 2xx URL. Raises DownloadError if a hop targets
    a private/loopback/link-local address, uses a non-http scheme, or
    if the chain exceeds _MAX_REDIRECTS.
    """
    from urllib.parse import urljoin

    from endless_library.url_safety import UnsafeUrlError, assert_safe_url

    try:
        assert_safe_url(url)
    except UnsafeUrlError as e:
        raise DownloadError(f"refused unsafe URL: {e}") from e

    current = url
    for _ in range(_MAX_REDIRECTS):
        r = client.head(current, follow_redirects=False)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location")
            if not loc:
                raise DownloadError(f"redirect from {current} with no Location header")
            current = urljoin(current, loc)
            try:
                assert_safe_url(current)
            except UnsafeUrlError as e:
                raise DownloadError(f"refused unsafe redirect target ({current!r}): {e}") from e
            continue
        if r.status_code >= 400:
            raise DownloadError(f"HTTP {r.status_code} from {current}")
        return current
    raise DownloadError(f"too many redirects (>{_MAX_REDIRECTS}) starting at {url}")


def download(
    handle: DownloadHandle,
    *,
    dest_dir: Path,
    fallback_name: str,
    expected_md5: str | None = None,
    client_factory=None,  # for tests
) -> DownloadResult:
    """Stream the URL to `<safe>.part`, atomic-rename on success. Verify MD5 if provided.

    Redirect handling: we walk the chain ourselves (no follow_redirects=True)
    so each hop's URL passes url_safety.assert_safe_url before we send a
    body-fetching GET. This blocks chained SSRF where a hostile shadow-
    library response 302s us at http://169.254.169.254/ (cloud metadata)
    or http://127.0.0.1:8090/ (our own dashboard).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = safe_filename(_filename_from_handle(handle, fallback_name))
    final = dest_dir / name
    part = final.with_suffix(final.suffix + ".part")

    factory = client_factory or (
        # follow_redirects=False on purpose; _walk_redirects does it manually
        # with per-hop URL safety assertion.
        lambda: httpx.Client(timeout=60.0, follow_redirects=False, headers=handle.headers)
    )
    md5 = hashlib.md5()
    total = 0
    content_type: str | None = None
    with factory() as client:
        safe_final_url = _walk_redirects(client, handle.url)
        with client.stream("GET", safe_final_url) as r:
            if r.status_code != 200:
                raise DownloadError(f"HTTP {r.status_code} from {safe_final_url}")
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
