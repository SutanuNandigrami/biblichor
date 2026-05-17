from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConvertResult:
    path: Path
    stderr_tail: str = ""


class ConvertError(Exception):
    pass


def convert_to_epub(
    src: Path,
    *,
    output_profile: str = "kindle_pw3",
    timeout_seconds: int = 300,
    min_output_bytes: int = 50_000,
    ebook_convert: str = "ebook-convert",
) -> ConvertResult:
    """Run ebook-convert to produce <src-stem>.epub. Returns the new path.

    Raises ConvertError on non-zero exit, timeout, or output below `min_output_bytes`.
    """
    if not src.exists():
        raise ConvertError(f"source not found: {src}")
    dest = src.with_suffix(".epub")
    cmd = [
        ebook_convert,
        str(src),
        str(dest),
        f"--output-profile={output_profile}",
        "--no-default-epub-cover",
    ]
    log.info("converting %s -> %s", src.name, dest.name)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
    except subprocess.TimeoutExpired as e:
        raise ConvertError(f"calibre timeout after {timeout_seconds}s") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        raise ConvertError(f"ebook-convert exit {proc.returncode}: {tail}")
    if not dest.exists() or dest.stat().st_size < min_output_bytes:
        size = dest.stat().st_size if dest.exists() else 0
        raise ConvertError(f"output too small or missing ({size}B)")
    return ConvertResult(path=dest, stderr_tail=(proc.stderr or "")[-500:])


def enrich_metadata(
    path: Path,
    *,
    title: str | None = None,
    author: str | None = None,
    series: str | None = None,
    tags: list[str] | None = None,
    isbn: str | None = None,
    language: str | None = None,
    ebook_meta: str = "ebook-meta",
    timeout_seconds: int = 60,
) -> None:
    """Edit metadata on an epub/azw3/mobi in place via Calibre's ebook-meta.

    Amazon's Send-to-Kindle reads the embedded metadata when ingesting
    personal docs; setting --authors/--series/--tags here makes books cluster
    correctly in the user's Kindle library even though all uploads land under
    the "Docs" category.
    """
    if not path.exists():
        raise ConvertError(f"source not found for metadata enrich: {path}")
    cmd: list[str] = [ebook_meta, str(path)]
    if title:
        cmd += ["--title", title]
    if author:
        cmd += ["--authors", author]
    if series:
        cmd += ["--series", series]
    if tags:
        cmd += ["--tags", ",".join(tags)]
    if isbn:
        cmd += ["--isbn", isbn]
    if language:
        cmd += ["--language", language]
    if len(cmd) <= 2:
        return  # nothing to set
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise ConvertError(f"ebook-meta timeout after {timeout_seconds}s") from e
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        raise ConvertError(f"ebook-meta exit {proc.returncode}: {tail}")


def add_to_calibre_library(
    path: Path,
    *,
    library_path: Path,
    series: str | None = None,
    tags: list[str] | None = None,
    calibredb: str = "calibredb",
    timeout_seconds: int = 60,
) -> int | None:
    """Add a downloaded book to a Calibre library via `calibredb add`.

    Returns the new book's id or None on failure. Non-fatal if Calibre is
    misconfigured (caller logs and continues).
    """
    if not path.exists():
        raise ConvertError(f"source not found for calibre import: {path}")
    library_path.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [calibredb, "add", "--library-path", str(library_path), str(path)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    import re as _re

    m = _re.search(r"Added book ids:\s*(\d+)", proc.stdout)
    book_id = int(m.group(1)) if m else None
    if book_id is None:
        return None
    if not series and not tags:
        return book_id
    set_metadata: list[str] = []
    if series:
        set_metadata += ["--field", f"series:{series}"]
    if tags:
        tag_joined = ",".join(tags)
        set_metadata += ["--field", f"tags:{tag_joined}"]
    if set_metadata:
        subprocess.run(
            [
                calibredb,
                "set_metadata",
                "--library-path",
                str(library_path),
                str(book_id),
                *set_metadata,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    return book_id
