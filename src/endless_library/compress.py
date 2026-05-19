"""Last-resort compression for files that don't fit the SMTP envelope.

For scanned PDFs (e.g. Bengali books from Anna's Archive — libtiff
producer), the existing PDF->EPUB rescue often produces a LARGER
EPUB because the scanned-image pages still dominate. ocrmypdf's
--optimize 3 path applies JBIG2 to B&W image runs and pngquant /
JPEG2000 to color regions, typically yielding 30-70% reduction.

For EPUBs over the cap, we unpack the zip, recompress each image
in-place with pngquant + jpegoptim, and repack. Useful for
illustrated EPUBs.

Both functions are best-effort: on any failure they leave the
original file untouched and raise CompressError so the caller can
fall through to the next strategy (or give up).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)


class CompressError(Exception):
    """Compression failed or produced a larger output. Caller should
    treat as a non-event and continue with the unmodified input."""


# ============ PDF compression ============


def compress_pdf(
    src: Path,
    *,
    aggressive: bool = False,
    timeout_seconds: int = 600,
    ocrmypdf_bin: str = "ocrmypdf",
) -> Path:
    """Compress a PDF via ocrmypdf optimize=3 + JBIG2.

    Returns a new Path next to src ending in `.opt.pdf`. Raises
    CompressError if the tool isn't installed, the run fails, or
    the output is larger than the input (no point shipping it).

    `aggressive=True` adds --jpeg-quality 50 which sacrifices color
    image quality for additional size. Use after the conservative
    pass if still oversize.

    `--skip-text` tells ocrmypdf to NOT run Tesseract OCR on pages
    that already have a text layer or don't need one — this is the
    fast path, ~5-30 s on a 20 MB book versus several minutes with
    OCR enabled. We're optimizing for size, not for searchability.
    """
    if not src.exists():
        raise CompressError(f"source not found: {src}")
    if shutil.which(ocrmypdf_bin) is None:
        raise CompressError(f"{ocrmypdf_bin} not installed")

    dst = src.with_suffix(".opt.pdf")
    cmd = [
        ocrmypdf_bin,
        "--optimize", "3",
        "--skip-text",
        "--jbig2-lossy",
        "--quiet",
    ]
    if aggressive:
        cmd += ["--jpeg-quality", "50", "--png-quality", "60"]
    cmd += [str(src), str(dst)]

    log.info("compress_pdf %s aggressive=%s", src.name, aggressive)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise CompressError(f"ocrmypdf timeout after {timeout_seconds}s") from e

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-400:]
        raise CompressError(f"ocrmypdf exit {proc.returncode}: {tail}")
    if not dst.exists():
        raise CompressError("ocrmypdf produced no output")
    src_size = src.stat().st_size
    dst_size = dst.stat().st_size
    if dst_size >= src_size:
        # No win — discard
        dst.unlink(missing_ok=True)
        raise CompressError(
            f"output not smaller: {src_size} -> {dst_size}"
        )
    return dst


# ============ EPUB compression ============


_IMG_EXTS = {".jpg", ".jpeg", ".png"}


def _shrink_jpeg(blob: bytes, *, quality: int) -> bytes | None:
    """Pipe a JPEG through jpegoptim and return the smaller version.
    Returns None on tool absence or no win."""
    if shutil.which("jpegoptim") is None:
        return None
    try:
        proc = subprocess.run(
            ["jpegoptim", "--strip-all", f"--max={quality}", "--stdout"],
            input=blob,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    if len(proc.stdout) >= len(blob):
        return None
    return proc.stdout


def _shrink_png(blob: bytes, *, quality_max: int) -> bytes | None:
    """Pipe a PNG through pngquant and return the smaller version."""
    if shutil.which("pngquant") is None:
        return None
    try:
        proc = subprocess.run(
            ["pngquant", "--quality", f"40-{quality_max}", "--speed", "1", "--strip", "--", "-"],
            input=blob,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    if len(proc.stdout) >= len(blob):
        return None
    return proc.stdout


def compress_epub(
    src: Path,
    *,
    jpeg_quality: int = 75,
    png_quality_max: int = 70,
) -> Path:
    """Recompress images inside an EPUB. Returns `<stem>.opt.epub`.

    Unzips into memory, replaces image entries with smaller versions
    where pngquant/jpegoptim manage it, repacks with the same zip
    structure. Raises CompressError if the result isn't smaller.
    """
    if not src.exists():
        raise CompressError(f"source not found: {src}")
    dst = src.with_suffix(".opt.epub")

    saved_bytes = 0
    src_size = src.stat().st_size
    try:
        with zipfile.ZipFile(src, "r") as zin, zipfile.ZipFile(
            dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9
        ) as zout:
            for info in zin.infolist():
                blob = zin.read(info.filename)
                ext = Path(info.filename).suffix.lower()
                if ext in {".jpg", ".jpeg"}:
                    smaller = _shrink_jpeg(blob, quality=jpeg_quality)
                    if smaller is not None:
                        saved_bytes += len(blob) - len(smaller)
                        blob = smaller
                elif ext == ".png":
                    smaller = _shrink_png(blob, quality_max=png_quality_max)
                    if smaller is not None:
                        saved_bytes += len(blob) - len(smaller)
                        blob = smaller
                # Preserve the original compression mode + permissions
                new_info = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
                zout.writestr(new_info, blob)
    except (zipfile.BadZipFile, OSError) as e:
        dst.unlink(missing_ok=True)
        raise CompressError(f"epub repack failed: {e}") from e

    dst_size = dst.stat().st_size
    if dst_size >= src_size:
        dst.unlink(missing_ok=True)
        raise CompressError(
            f"output not smaller: {src_size} -> {dst_size} (saved {saved_bytes}B in images)"
        )
    return dst


# ============ One-call dispatcher for the pipeline ============


def try_compress(src: Path, *, target_bytes: int) -> Path | None:
    """Try the available strategies for this file's extension until
    the output fits target_bytes. Returns the new Path if successful,
    None if no strategy worked. The original file is never touched.

    For PDFs: ocrmypdf conservative -> aggressive.
    For EPUBs: pngquant + jpegoptim recompress.
    Unknown extensions: returns None immediately.
    """
    ext = src.suffix.lower()
    if ext == ".pdf":
        for aggressive in (False, True):
            try:
                out = compress_pdf(src, aggressive=aggressive)
            except CompressError as e:
                log.info("compress_pdf aggressive=%s gave up: %s", aggressive, e)
                continue
            if out.stat().st_size <= target_bytes:
                log.info(
                    "compress_pdf aggressive=%s: %d -> %d bytes (fits)",
                    aggressive,
                    src.stat().st_size,
                    out.stat().st_size,
                )
                return out
            # Smaller but still over cap — keep going with aggressive
            out.unlink(missing_ok=True)
        return None
    if ext == ".epub":
        try:
            out = compress_epub(src)
        except CompressError as e:
            log.info("compress_epub gave up: %s", e)
            return None
        if out.stat().st_size <= target_bytes:
            return out
        out.unlink(missing_ok=True)
        return None
    return None
