"""High-level: take a freshly-downloaded file, return a safe ebook path.

Use case: kindlebangla's "single-file" Drive items download as a RAR
archive wrapping the actual EPUB. Most other sources stream the ebook
directly. We:

  1. Detect whether the downloaded file is itself an archive
     (RAR or non-EPUB ZIP). Bare .epub IS a ZIP — we recognize that
     via the embedded `mimetype` and pass it through unchanged.
  2. Apply the hygiene rules from archive_safety (always-on).
  3. Optionally run ClamAV (clamav.scan). When require_clamav=True
     and ClamAV is missing, we hard-fail; otherwise log+continue.
  4. Return the path to the extracted ebook. Caller cleans up the
     original archive afterwards.

Any rule trip raises ArchiveSafetyError (or UnpackError below), which
the pipeline catches and converts to a failed book with the reason
written to events.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from endless_library.security.archive_safety import (
    ArchiveSafetyError,
    SafetyLimits,
    detect_archive,
    is_epub_zip,
    safe_extract_rar,
    safe_extract_zip,
)
from endless_library.security.clamav import ScanResult
from endless_library.security.clamav import scan as clamav_scan

log = logging.getLogger(__name__)


class UnpackError(Exception):
    """Raised by unpack_if_archive when an archive fails safety, AV, or
    extraction. Pipeline turns this into a `failed` book with the message."""


@dataclass(frozen=True, slots=True)
class UnpackResult:
    path: Path  # the ebook ready for convert + send
    was_archive: bool
    av_result: ScanResult | None  # None if file was already a bare ebook


def unpack_if_archive(
    downloaded_path: Path,
    *,
    limits: SafetyLimits | None = None,
    require_clamav: bool = False,
    extraction_dir: Path | None = None,
) -> UnpackResult:
    """Pass-through for bare ebooks, hygiene+AV-enforced extraction for
    real archives.

    Args:
        downloaded_path: file just streamed to disk.
        limits: hygiene limits (size caps, member count) — defaults sensible.
        require_clamav: when True, hard-fail if clamscan isn't installed.
            When False (the default), the user is warned but the file is
            allowed once it has passed the hygiene rules.
        extraction_dir: where to drop extracted files. Defaults to a sibling
            directory named "<filename>.unpacked".

    Returns:
        UnpackResult(path, was_archive, av_result)

    Raises:
        UnpackError: archive failed hygiene, was infected, or required AV
            scan couldn't run.
    """
    if not downloaded_path.exists():
        raise UnpackError(f"file does not exist: {downloaded_path}")

    archive_kind = detect_archive(downloaded_path)

    # Bare .epub is technically a ZIP but doesn't need unpacking — recognize
    # it via the embedded `mimetype` and bail early.
    if archive_kind == "zip" and is_epub_zip(downloaded_path):
        archive_kind = None  # treat as pass-through

    if archive_kind is None:
        # Not an archive (or it's a bare EPUB) — apply AV directly to the
        # downloaded file. Hygiene rules don't apply (nothing was unpacked).
        av = clamav_scan(downloaded_path, require=require_clamav)
        if not av.ok:
            raise UnpackError(f"AV scan failed: {av.threat or av.detail}")
        return UnpackResult(path=downloaded_path, was_archive=False, av_result=av)

    # It's an archive. Hygiene-extract first.
    dest = extraction_dir or (downloaded_path.parent / (downloaded_path.name + ".unpacked"))
    if dest.exists():
        # Avoid clobbering a previous attempt
        shutil.rmtree(dest)
    try:
        if archive_kind == "zip":
            ebook_path = safe_extract_zip(downloaded_path, dest_dir=dest, limits=limits)
        elif archive_kind == "rar":
            ebook_path = safe_extract_rar(downloaded_path, dest_dir=dest, limits=limits)
        else:
            raise UnpackError(f"unsupported archive kind: {archive_kind}")
    except ArchiveSafetyError as e:
        # Clean up partial extraction
        shutil.rmtree(dest, ignore_errors=True)
        raise UnpackError(f"archive hygiene violation: {e}") from e

    # AV-scan the extracted ebook.
    av = clamav_scan(ebook_path, require=require_clamav)
    if not av.ok:
        shutil.rmtree(dest, ignore_errors=True)
        raise UnpackError(f"AV scan rejected extracted file: {av.threat or av.detail}")

    # Phase 6u.5c: collision-proof extracted-file naming.
    #
    # Old behaviour used `ebook_path.name` (the filename *inside* the
    # archive) as the destination basename. kindlebangla RARs frequently
    # share or near-share inner filenames across different books (after
    # filesystem normalisation), so book B silently overwrote book A's
    # extracted EPUB and stale-ed every book.file_path that pointed at
    # it. Symptom: ebook-meta exit 1, FileNotFoundError on resume.
    #
    # New: rename the original archive to <name>.orig first (freeing
    # the original slot), then move the extracted file into that slot.
    # The downloaded filename is derived from the provider's unique
    # identifier (kindlebangla slug, Anna's md5, etc) so collisions are
    # structurally impossible.
    try:
        downloaded_path.rename(
            downloaded_path.with_suffix(downloaded_path.suffix + ".orig")
        )
    except OSError as e:
        log.warning(
            "unpack: could not preserve original at %s.orig: %s",
            downloaded_path,
            e,
        )
        raise UnpackError(
            f"refused to overwrite {downloaded_path} without successful .orig preservation"
        ) from e

    final_path = downloaded_path  # original slot, now free
    if final_path.exists():
        # Belt-and-suspenders: any stragglers in this slot drop now.
        final_path.unlink()
    shutil.move(str(ebook_path), str(final_path))
    shutil.rmtree(dest, ignore_errors=True)

    return UnpackResult(path=final_path, was_archive=True, av_result=av)
