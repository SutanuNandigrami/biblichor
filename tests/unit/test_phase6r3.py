"""Phase 6r.3: pin container binary requirements.

Bugs found during the systemd->container audit:
  - `age` and `age-keygen` were missing - backup-key + encrypted
    backup failed
  - `zstd` was missing - even unencrypted backup failed at the
    compress step

Both surfaced only at backup time, so the cutover looked healthy
until the user actually tried to back up.

These tests parse the Dockerfile and assert every binary that
endless_library code shells out to is in the apt-get install line.
Doesn't run the container; cheap unit test."""

from __future__ import annotations

from pathlib import Path


def _dockerfile_text() -> str:
    return (Path(__file__).parent.parent.parent / "deploy" / "Dockerfile").read_text()


# Phase 6r.3: these binaries are shelled out to by endless_library code.
# Each one MUST be installed via apt-get (or built from source) in the
# Dockerfile, or the corresponding feature breaks silently inside the
# container.
REQUIRED_BINARIES = {
    "calibre": "ebook-convert / ebook-meta / calibredb for format conversion + metadata",
    "ocrmypdf": "scanned PDF -> searchable PDF rescue",
    "pngquant": "EPUB image compress for SMTP fit",
    "jpegoptim": "EPUB image compress",
    "qpdf": "PDF linearization (pre-OCR)",
    "mupdf-tools": "mutool for PDF inspection",
    "unar": "archive extraction (some sources ship books in RAR/ZIP)",
    "age": "backup encryption (biblichor backup-key, biblichor backup)",
    "zstd": "backup compression (tar+zst, the format biblichor backup uses)",
    "curl": "Dockerfile healthcheck",
    "ca-certificates": "SSL for httpx",
}


def test_dockerfile_installs_all_required_binaries():
    """Every binary biblichor shells out to must be in the apt-get
    install list. Catches the 6r.3-class bug where a `subprocess.run`
    call fails inside the container because the OS package wasn't
    layered in."""
    dockerfile = _dockerfile_text()
    missing = []
    for pkg in REQUIRED_BINARIES:
        if pkg not in dockerfile:
            missing.append(pkg)
    assert not missing, (
        "Dockerfile missing apt packages required by code:\n"
        + "\n".join(f"  - {p}: {REQUIRED_BINARIES[p]}" for p in missing)
    )


def test_backup_module_imports_zstandard():
    """The Python `zstandard` library is the streaming-zst path the
    backup module uses. It must be in deps (not just the CLI `zstd`)."""
    import importlib

    importlib.import_module("zstandard")  # must not raise


def test_cryptography_is_a_runtime_dep_not_optional():
    """`cryptography` lib is used by the AES-GCM secrets store (Phase 6p.1)
    and must be in `dependencies`, not `[project.optional-dependencies]`."""
    pp = (Path(__file__).parent.parent.parent / "pyproject.toml").read_text()
    # Find the dependencies block. Split on `\n]` (the closing
    # bracket on its own line) because individual entries like
    # `uvicorn[standard]` contain a `]` mid-string.
    deps_block = pp.split("dependencies = [", 1)[1].split("\n]", 1)[0]
    assert "cryptography" in deps_block, (
        "cryptography must be in [project.dependencies], not optional"
    )
