"""Regression + feature-intact tests for the drop_into_library helper
(Phase 6c) + the pipeline wiring contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from endless_library.bookorbit.drop import (
    BookOrbitDropError,
    drop_into_library,
)


@pytest.fixture
def src(tmp_path):
    f = tmp_path / "book.epub"
    f.write_bytes(b"epub data here")
    return f


@pytest.fixture
def library_root(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    return root


# ============ FEATURE-INTACT: layout correctness ============


def test_book_per_folder_creates_author_title_subdirs(src, library_root):
    result = drop_into_library(
        src,
        library_root=library_root,
        title="The Pragmatic Programmer",
        author="Hunt",
        organization_mode="book_per_folder",
    )
    # Expected layout: <root>/Hunt/The Pragmatic Programmer/book.epub
    assert result.target_path.exists()
    rel = result.target_path.relative_to(library_root)
    parts = rel.parts
    assert parts[0] == "Hunt"
    assert "Pragmatic" in parts[1]
    assert parts[-1] == "book.epub"


def test_book_per_file_drops_flat(src, library_root):
    result = drop_into_library(
        src,
        library_root=library_root,
        title="anything",
        author="anyone",
        organization_mode="book_per_file",
    )
    assert result.target_path.parent == library_root
    assert result.target_path.name == "book.epub"


def test_content_round_trips(src, library_root):
    drop_into_library(src, library_root=library_root, title="T", author="A")
    target = library_root / "A" / "T" / "book.epub"
    assert target.read_bytes() == b"epub data here"


def test_bytes_written_returned(src, library_root):
    r = drop_into_library(src, library_root=library_root, title="T", author="A")
    assert r.bytes_written == len(b"epub data here")


# ============ REGRESSION: Unicode survives ============


def test_bengali_title_and_author_preserved(src, library_root):
    """Crucial — the whole reason Phase X.i exists. Filesystem layout
    must keep Bengali script."""
    r = drop_into_library(
        src,
        library_root=library_root,
        title="কাঁটায়-কাঁটায় ৬",
        author="নারায়ণ সান্যাল",
    )
    rel = r.target_path.relative_to(library_root)
    assert "কাঁটায়" in str(rel)


# ============ REGRESSION: hostile inputs ============


def test_path_traversal_in_title_neutralized(src, library_root):
    """Even if a malformed title contains ../../, safe_filename
    strips it so we never write outside the library root."""
    r = drop_into_library(
        src,
        library_root=library_root,
        title="../../etc",
        author="../../passwd",
    )
    # Result MUST still be inside library_root
    assert library_root.resolve() in r.target_path.resolve().parents


def test_missing_source_raises_clear_error(tmp_path, library_root):
    with pytest.raises(BookOrbitDropError, match="source not found"):
        drop_into_library(
            tmp_path / "nope.epub",
            library_root=library_root,
            title="x",
            author="y",
        )


def test_missing_library_root_raises_clear_error(src, tmp_path):
    with pytest.raises(BookOrbitDropError, match="library root not found"):
        drop_into_library(
            src,
            library_root=tmp_path / "absent-dir",
            title="x",
            author="y",
        )


def test_unknown_organization_mode_raises(src, library_root):
    with pytest.raises(BookOrbitDropError, match="unknown organization_mode"):
        drop_into_library(
            src, library_root=library_root, title="x", author="y",
            organization_mode="random-thing",
        )


def test_unknown_author_defaults_to_unknown_dir(src, library_root):
    r = drop_into_library(src, library_root=library_root, title="x", author=None)
    rel = r.target_path.relative_to(library_root)
    assert rel.parts[0] == "Unknown"


def test_drop_is_idempotent(src, library_root):
    """Re-dropping the same book overwrites cleanly (caller's
    responsibility to dedupe upstream — BookOrbit also dedupes via
    SHA on its side)."""
    r1 = drop_into_library(src, library_root=library_root, title="t", author="a")
    r2 = drop_into_library(src, library_root=library_root, title="t", author="a")
    assert r1.target_path == r2.target_path
    assert r2.target_path.exists()


# ============ CONFIG CONTRACT ============


def test_bookorbit_cfg_defaults_to_disabled():
    """Existing biblichor installs without bookorbit must keep working.
    Default enabled=false ensures the pipeline is a no-op."""
    from endless_library.config import BookOrbitCfg
    cfg = BookOrbitCfg()
    assert cfg.enabled is False
    assert cfg.organization_mode == "book_per_folder"


def test_config_now_exposes_bookorbit_section():
    """Config dataclass round-trips a BookOrbitCfg field."""
    from endless_library.config import BookOrbitCfg, Config
    c = Config()
    assert isinstance(c.bookorbit, BookOrbitCfg)
    assert c.bookorbit.enabled is False
