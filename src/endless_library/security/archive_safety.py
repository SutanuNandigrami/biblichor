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
        ".azw",
        ".mobi",
        ".pdf",
        ".kfx",  # Amazon Kindle-native (kindlebangla packs these for some titles)
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",  # covers
        ".opf",
        ".ncx",
        ".xml",  # metadata
        ".txt",  # readme/note files inside the archive
        ".cbz",
        ".cbr",  # comic-book archives (we already gate nested archives)
    }
)

EBOOK_EXTENSIONS: tuple[str, ...] = (
    ".epub",
    ".azw3",
    ".kfx",
    ".azw",
    ".mobi",
    ".pdf",
    ".cbz",
    ".cbr",
)

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
    """Security-only check. Raises ArchiveSafetyError on path traversal,
    absolute paths, or nested-archive bombs. Disallowed FORMAT extensions
    (e.g. `.kfx`, `.original_kfx`, `.jpg` siblings of the EPUB) are NOT
    raised here — use `_member_is_extractable_format` to decide whether
    to bother extracting that member.

    Phase 6u.5d: directory entries (trailing '/') skipped.
    Phase 6u.7c: format check split from security check — kindlebangla
    RARs commonly contain `.epub + .kfx + .jpg + .opf + .original_kfx`
    siblings. Failing the whole archive on a `.kfx` extension loses the
    EPUB we actually want.
    Phase 6y M10: normalize backslashes to forward slashes before the
    traversal check so legacy Windows CBR members (e.g. 'ch01/page01.jpg')
    are accepted. Absolute-path check is done after normalisation."""
    if not name:
        raise ArchiveSafetyError("empty member name")
    # M10: normalise Windows-style separators before any security check.
    name = name.replace("\\", "/")
    if name.startswith("/"):
        raise ArchiveSafetyError(f"absolute-path member: {name!r}")
    parts = name.split("/")
    if any(p in ("..", "") for p in parts[:-1]):
        raise ArchiveSafetyError(f"path traversal: {name!r}")
    if any(p == ".." for p in parts):
        raise ArchiveSafetyError(f"path traversal: {name!r}")

    # Directory entries: trailing slash → no file to extract. Skip.
    if name.endswith("/") or not parts[-1]:
        return

    suffix = Path(parts[-1]).suffix.lower()
    # Nested archives ARE a security issue (zip bombs, payload smuggling).
    if suffix in NESTED_ARCHIVE_EXTENSIONS:
        raise ArchiveSafetyError(f"nested archive forbidden: {name!r}")
    # Disallowed format extensions are no longer fatal at the security
    # layer — they're filtered at extraction time below.


def _member_is_extractable_format(name: str) -> bool:
    """Per-member format check. Skip directory entries and any file
    whose extension isn't in ALLOWED_EXTENSIONS. Used to filter the
    member list before extraction so a `.kfx` sibling doesn't kill the
    archive."""
    if not name or name.endswith("/"):
        return False
    parts = name.replace("\\", "/").split("/")
    if not parts[-1]:
        return False
    suffix = Path(parts[-1]).suffix.lower()
    if not suffix:
        return False
    return suffix in ALLOWED_EXTENSIONS


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

    # Phase 6u.7: same lazy-error treatment as RAR. zipfile sometimes
    # raises BadZipFile on infolist()/extract() rather than open(), so
    # we wrap the entire read+extract surface.
    try:
        zf = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ArchiveSafetyError(f"invalid ZIP: {e}") from e
    with zf as z:
        try:
            members = z.infolist()
        except (zipfile.BadZipFile, OSError) as e:
            raise ArchiveSafetyError(f"truncated or corrupt ZIP: {e}") from e
        if len(members) > limits.max_members:
            raise ArchiveSafetyError(f"too many members ({len(members)} > {limits.max_members})")
        total_uncompressed = 0
        for info in members:
            _check_member_safe(info.filename)
            total_uncompressed += info.file_size
        # Phase 6u.7c: filter to extractable-format members so .kfx /
        # .opf / .jpg siblings don't drag the archive down.
        members = [m for m in members if _member_is_extractable_format(m.filename)]
        if not members:
            raise ArchiveSafetyError("no extractable ebook member in ZIP")
        max_extracted = limits.max_extracted_size_mb * 1024 * 1024
        if total_uncompressed > max_extracted:
            raise ArchiveSafetyError(
                f"uncompressed size ({total_uncompressed}) exceeds cap ({max_extracted})"
            )

        def _extract_one_zip(member, target_dir):
            try:
                z.extract(member, target_dir)
            except (zipfile.BadZipFile, OSError) as e:
                raise ArchiveSafetyError(f"ZIP extract failed: {e}") from e

        return _extract_and_pick(
            extractor=_extract_one_zip,
            members=[m.filename for m in members],
            dest_dir=dest_dir,
        )


def safe_extract_rar(
    archive_path: Path,
    *,
    dest_dir: Path,
    limits: SafetyLimits | None = None,
) -> Path:
    """Extract a RAR under the hygiene rules.

    Phase 6u.7b: switched extraction backend from `rarfile` (Python) to
    `bsdtar` (libarchive shell-out). Background:

      The kindlebangla flood surfaced ~7% of downloads where Google
      Drive returns the same truncated payload on every retry (deterministic
      truncation, not transient network loss). rarfile refuses any
      partial archive — `infolist()` raises `BadRarFile` and the whole
      book is lost — even though the truncation typically only corrupts
      the cover JPG; the actual EPUB member is intact.

      `bsdtar` (and `unar`) skip corrupt members and continue extracting
      the rest. With the new backend, ~all of those books recover their
      EPUB. Same RAR archives that previously yielded
      `BadRarFile("Failed the read enough data: req=N got=0")` now extract
      cleanly.

    The hygiene rules still apply: total size cap, member count cap,
    extension allowlist via `_check_member_safe`. We list members
    through `bsdtar -tf` (also more tolerant than rarfile.infolist),
    walk them past hygiene, then extract with `bsdtar -xf`.
    """
    import shutil
    import subprocess

    limits = limits or SafetyLimits()
    _check_archive_size(archive_path, limits)

    bsdtar = shutil.which("bsdtar")
    if not bsdtar:
        # Without bsdtar we fall back to the Python rarfile route. Same
        # historical brittleness, but better than nothing.
        return _safe_extract_rar_rarfile(archive_path, dest_dir=dest_dir, limits=limits)

    # List members. bsdtar handles truncated/corrupt RARs gracefully.
    try:
        listing = subprocess.run(
            [bsdtar, "-tf", str(archive_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as e:
        raise ArchiveSafetyError(f"bsdtar list timed out: {e}") from e
    if listing.returncode != 0 and not listing.stdout:
        raise ArchiveSafetyError(
            f"bsdtar could not read RAR: {(listing.stderr or '').strip()[:200]}"
        )

    raw_names = [m for m in (line.rstrip("\r") for line in listing.stdout.splitlines()) if m]
    if len(raw_names) > limits.max_members:
        raise ArchiveSafetyError(f"too many members ({len(raw_names)} > {limits.max_members})")
    for name in raw_names:
        _check_member_safe(name)  # security-only (raises on traversal etc.)
    # Phase 6u.7c: filter to extractable-format members only. Skip
    # directories, .kfx/.jpg/.opf siblings, .original_kfx, etc.
    member_names = [n for n in raw_names if _member_is_extractable_format(n)]
    if not member_names:
        raise ArchiveSafetyError(f"no extractable ebook member in archive (saw {raw_names!r})")

    # bsdtar doesn't report uncompressed size in -tf output without -v.
    # Run a -tvf for size accounting — still fast (metadata only).
    try:
        verbose = subprocess.run(
            [bsdtar, "-tvf", str(archive_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        total_uncompressed = 0
        for line in verbose.stdout.splitlines():
            parts = line.split()
            # bsdtar -tvf: perms uid/gid size mon day time name
            if len(parts) >= 5 and parts[2].isdigit():
                total_uncompressed += int(parts[2])
        max_extracted = limits.max_extracted_size_mb * 1024 * 1024
        if total_uncompressed > max_extracted:
            raise ArchiveSafetyError(
                f"uncompressed size ({total_uncompressed}) exceeds cap ({max_extracted})"
            )
    except subprocess.TimeoutExpired:
        pass  # Best-effort; main path is the list+extract steps above/below.

    dest_dir.mkdir(parents=True, exist_ok=True)

    def _extract_one(member, target_dir):
        # Extract this specific member. bsdtar exits 0 on success even
        # if SOME members in the archive are corrupt; it fails only when
        # *this* member can't be extracted at all.
        try:
            r = subprocess.run(
                [bsdtar, "-xf", str(archive_path), "-C", str(target_dir), member],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as e:
            raise ArchiveSafetyError(f"bsdtar extract timed out: {e}") from e
        if r.returncode != 0:
            raise ArchiveSafetyError(f"bsdtar extract failed: {(r.stderr or '').strip()[:200]}")
        # Post-extraction path safety check: ensure bsdtar didn't write
        # anything outside target_dir (e.g. via relative-path escapes
        # that slipped past _check_member_safe).
        _verify_extracted_paths_safe(Path(target_dir))

    return _extract_and_pick(
        extractor=_extract_one,
        members=member_names,
        dest_dir=dest_dir,
    )


def _safe_extract_rar_rarfile(
    archive_path: Path,
    *,
    dest_dir: Path,
    limits: SafetyLimits,
) -> Path:
    """Legacy rarfile-based extraction. Used as a fallback when bsdtar
    isn't on PATH. Strict — refuses partial archives."""
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

    try:
        members = rf.infolist()
    except rarfile.Error as e:
        raise ArchiveSafetyError(f"truncated or corrupt RAR: {e}") from e

    if len(members) > limits.max_members:
        raise ArchiveSafetyError(f"too many members ({len(members)} > {limits.max_members})")
    total_uncompressed = 0
    for info in members:
        _check_member_safe(info.filename)
        total_uncompressed += getattr(info, "file_size", 0) or 0
    max_extracted = limits.max_extracted_size_mb * 1024 * 1024
    if total_uncompressed > max_extracted:
        raise ArchiveSafetyError(
            f"uncompressed size ({total_uncompressed}) exceeds cap ({max_extracted})"
        )
    # Phase 6u.7c: filter to extractable-format members only.
    members = [m for m in members if _member_is_extractable_format(m.filename)]
    if not members:
        raise ArchiveSafetyError("no extractable ebook member in RAR")

    def _extract_one(member, target_dir):
        try:
            rf.extract(member, target_dir)
        except rarfile.Error as e:
            raise ArchiveSafetyError(f"RAR extract failed: {e}") from e

    return _extract_and_pick(
        extractor=_extract_one,
        members=[m.filename for m in members],
        dest_dir=dest_dir,
    )


def _verify_extracted_paths_safe(dest_dir: Path) -> None:
    """Post-extraction guard: verify every file bsdtar wrote actually
    lives under dest_dir. bsdtar strips leading slashes by default, but
    archive members with relative escapes (e.g. ``../../etc/passwd``)
    could still resolve outside the destination after joining. Remove
    any escapee and raise ArchiveSafetyError.

    This is a second line of defence behind _check_member_safe; call it
    after each bsdtar -xf completes.
    """
    dest_resolved = dest_dir.resolve()
    for p in dest_dir.rglob("*"):
        if not p.is_file():
            continue
        try:
            real = p.resolve()
            real.relative_to(dest_resolved)
        except (OSError, ValueError):
            log.warning("archive_safety: removing extracted-outside-dest file: %s", p)
            p.unlink(missing_ok=True)
            raise ArchiveSafetyError(f"archive escaped dest_dir: {p}")  # noqa: B904


def _check_archive_size(path: Path, limits: SafetyLimits) -> None:
    sz = path.stat().st_size
    cap = limits.max_archive_size_mb * 1024 * 1024
    if sz > cap:
        raise ArchiveSafetyError(f"archive too large ({sz} > {cap})")


def _extract_and_pick(*, extractor, members: list[str], dest_dir: Path) -> Path:
    """Extract every safe member into dest_dir, then return the path to the
    single ebook file.

    Phase 6u.7b: tolerant of per-member extract failures. A truncated
    archive (kindlebangla/Google-Drive deterministic truncation) may
    have one corrupt member (often the cover JPG) while the EPUB
    intended for delivery is intact. We collect all successfully
    extracted files and pick the best ebook from whatever made it out.
    Only fail if NO ebook member extracted.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    member_errors: list[str] = []
    for name in members:
        try:
            extractor(name, dest_dir)
        except ArchiveSafetyError as e:
            member_errors.append(f"{name}: {e}")
            continue
        # The extracted path is dest_dir / <name>. Track everything we wrote
        # so the caller can clean up on failure.
        out = dest_dir / name
        if out.exists():
            extracted.append(out)

    # Pick the ebook by extension priority
    for ext in EBOOK_EXTENSIONS:
        for p in extracted:
            if p.is_file() and p.suffix.lower() == ext:
                return p
    detail = "; ".join(member_errors)[:200] if member_errors else "no ebook member"
    raise ArchiveSafetyError(
        f"no ebook file in archive (extracted: {[p.name for p in extracted]}; errors: {detail})"
    )
