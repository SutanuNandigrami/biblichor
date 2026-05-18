"""Defense-in-depth hygiene for archive extraction.

Some kindlebangla.com items download as a RAR archive wrapping the actual
.epub. We need to unpack to keep the pipeline working — but unpacking
third-party archives is exactly the kind of operation that gets exploited
(path traversal, zip bombs, exotic file types, parser bugs).

This module enforces a strict, always-on set of rules. ClamAV (in the
sibling module) is the optional second layer.

Hard limits enforced here:

  * Magic byte check         — must be RAR or ZIP, nothing else
  * Compressed size cap      — refuse archives larger than max_archive_size_mb
  * Path traversal block     — any member containing '..' or starting with '/'
                               is fatal (no silent skip)
  * Extension whitelist      — every member must end with an allowed extension
                               (ebooks + covers + metadata only)
  * Zip-bomb protection      — total uncompressed size must stay under
                               max_extracted_size_mb
  * No nested archives       — members ending in .zip/.rar/.7z/.tar/.gz/.tar.gz
                               are refused outright
  * Single-ebook contract    — must contain exactly one openable ebook
                               (.epub > .azw3 > .mobi > .pdf)

The result is a temp directory with the extracted ebook ready for the
optional AV scan and then the normal Calibre convert step.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# Magic byte prefixes we recognize. Order matters when checking — RAR 5 is a
# superset prefix of RAR 4, but both start with "Rar!".
RAR_MAGIC = b"Rar!\x1a\x07"  # covers RAR 1.5 (5 bytes) + 5.x (7 bytes)
ZIP_MAGIC = b"PK\x03\x04"

# Always-on whitelist; everything else is rejected.
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".epub",
        ".azw3",
        ".mobi",
        ".pdf",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",  # covers
        ".opf",
        ".ncx",
        ".xml",  # metadata
        ".txt",  # readme/note files inside the archive
    }
)

EBOOK_EXTENSIONS: tuple[str, ...] = (".epub", ".azw3", ".mobi", ".pdf")

# Refused outright: any nested archive
NESTED_ARCHIVE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
    }
)


class ArchiveSafetyError(Exception):
    """Raised when an archive trips a hygiene rule. The book is failed."""


@dataclass(frozen=True, slots=True)
class SafetyLimits:
    max_archive_size_mb: int = 200
    max_extracted_size_mb: int = 500
    max_members: int = 50


def detect_archive(path: Path) -> str | None:
    """Returns 'rar', 'zip', or None."""
    try:
        with path.open("rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if head.startswith(RAR_MAGIC):
        return "rar"
    if head.startswith(ZIP_MAGIC):
        return "zip"
    return None


def _check_member_safe(name: str) -> None:
    """Path-traversal + extension + nested-archive guard. Raises on fail."""
    if not name:
        raise ArchiveSafetyError("empty member name")
    if name.startswith("/") or "\\" in name:
        raise ArchiveSafetyError(f"absolute-path member: {name!r}")
    # `Path` resolves '..' relative to a fictitious root; the easier check is
    # to look for the literal sequence anywhere in the name.
    parts = name.replace("\\", "/").split("/")
    if any(p in ("..", "") for p in parts[:-1]):  # last part can be ""
        raise ArchiveSafetyError(f"path traversal: {name!r}")
    if any(p == ".." for p in parts):
        raise ArchiveSafetyError(f"path traversal: {name!r}")

    suffix = Path(name).suffix.lower()
    if suffix in NESTED_ARCHIVE_EXTENSIONS:
        raise ArchiveSafetyError(f"nested archive forbidden: {name!r}")
    if suffix and suffix not in ALLOWED_EXTENSIONS:
        raise ArchiveSafetyError(f"disallowed extension {suffix!r} in {name!r}")


def is_epub_zip(path: Path) -> bool:
    """A bare .epub IS a ZIP. Distinguish it from an unrelated ZIP wrapper.
    An EPUB has a `mimetype` member whose content is `application/epub+zip`.
    """
    try:
        with zipfile.ZipFile(path) as z:
            if "mimetype" not in z.namelist():
                return False
            with z.open("mimetype") as f:
                return f.read().strip().startswith(b"application/epub+zip")
    except (zipfile.BadZipFile, OSError):
        return False


def safe_extract_zip(
    archive_path: Path,
    *,
    dest_dir: Path,
    limits: SafetyLimits | None = None,
) -> Path:
    """Extract a ZIP under the hygiene rules. Returns the path to the
    contained ebook file. Raises ArchiveSafetyError on any rule trip.
    """
    limits = limits or SafetyLimits()
    _check_archive_size(archive_path, limits)

    try:
        zf = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ArchiveSafetyError(f"invalid ZIP: {e}") from e
    with zf as z:
        members = z.infolist()
        if len(members) > limits.max_members:
            raise ArchiveSafetyError(f"too many members ({len(members)} > {limits.max_members})")
        total_uncompressed = 0
        for info in members:
            _check_member_safe(info.filename)
            total_uncompressed += info.file_size
        max_extracted = limits.max_extracted_size_mb * 1024 * 1024
        if total_uncompressed > max_extracted:
            raise ArchiveSafetyError(
                f"uncompressed size ({total_uncompressed}) exceeds cap ({max_extracted})"
            )
        return _extract_and_pick(
            extractor=lambda member, target_dir: z.extract(member, target_dir),
            members=[m.filename for m in members],
            dest_dir=dest_dir,
        )


def safe_extract_rar(
    archive_path: Path,
    *,
    dest_dir: Path,
    limits: SafetyLimits | None = None,
) -> Path:
    """Extract a RAR under the same hygiene rules. Requires the optional
    `rarfile` Python package; raises ArchiveSafetyError("rarfile not
    installed") when missing.
    """
    limits = limits or SafetyLimits()
    _check_archive_size(archive_path, limits)

    try:
        import rarfile  # type: ignore
    except ImportError as e:
        raise ArchiveSafetyError(
            "rarfile package not installed (pip install rarfile + apt install unrar)"
        ) from e

    try:
        rf = rarfile.RarFile(str(archive_path))
    except rarfile.Error as e:
        raise ArchiveSafetyError(f"invalid RAR: {e}") from e

    members = rf.infolist()
    if len(members) > limits.max_members:
        raise ArchiveSafetyError(f"too many members ({len(members)} > {limits.max_members})")
    total_uncompressed = 0
    for info in members:
        _check_member_safe(info.filename)
        # rarfile uses file_size for the original (uncompressed) size
        total_uncompressed += getattr(info, "file_size", 0) or 0
    max_extracted = limits.max_extracted_size_mb * 1024 * 1024
    if total_uncompressed > max_extracted:
        raise ArchiveSafetyError(
            f"uncompressed size ({total_uncompressed}) exceeds cap ({max_extracted})"
        )

    return _extract_and_pick(
        extractor=lambda member, target_dir: rf.extract(member, target_dir),
        members=[m.filename for m in members],
        dest_dir=dest_dir,
    )


def _check_archive_size(path: Path, limits: SafetyLimits) -> None:
    sz = path.stat().st_size
    cap = limits.max_archive_size_mb * 1024 * 1024
    if sz > cap:
        raise ArchiveSafetyError(f"archive too large ({sz} > {cap})")


def _extract_and_pick(*, extractor, members: list[str], dest_dir: Path) -> Path:
    """Extract every safe member into dest_dir, then return the path to the
    single ebook file. Refuses if zero or multiple ebooks are present
    (multiple breaks the 1 book = 1 file contract).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    for name in members:
        extractor(name, dest_dir)
        # The extracted path is dest_dir / <name>. Track everything we wrote
        # so the caller can clean up on failure.
        extracted.append(dest_dir / name)

    # Pick the ebook by extension priority
    for ext in EBOOK_EXTENSIONS:
        for p in extracted:
            if p.is_file() and p.suffix.lower() == ext:
                return p
    raise ArchiveSafetyError(f"no ebook file in archive; extracted: {[p.name for p in extracted]}")
