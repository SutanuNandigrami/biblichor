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
