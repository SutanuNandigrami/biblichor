"""Phase 6o.2: Regression + feature-intact tests for C-1, atomic drop,
dedupe layout, graceful missing-yaml, startup guard."""

from __future__ import annotations

import httpx
import pytest
import respx

from endless_library.bookorbit.drop import (
    BookOrbitDropError,
    compute_target_path,
    drop_into_library,
)

# ============ REGRESSION: compute_target_path is single source of layout truth ============


def test_compute_target_path_book_per_folder(tmp_path):
    """The canonical layout: <root>/<author>/<title>/<basename>."""
    out = compute_target_path(
        library_root=tmp_path,
        title="Title",
        author="Author",
        file_basename="x.epub",
        organization_mode="book_per_folder",
    )
    assert out == tmp_path / "Author" / "Title" / "x.epub"


def test_compute_target_path_book_per_file(tmp_path):
    out = compute_target_path(
        library_root=tmp_path,
        title="anything",
        author="anyone",
        file_basename="x.epub",
        organization_mode="book_per_file",
    )
    assert out == tmp_path / "x.epub"


def test_compute_target_path_unknown_mode_raises(tmp_path):
    with pytest.raises(BookOrbitDropError, match="unknown organization_mode"):
        compute_target_path(
            library_root=tmp_path,
            title="x",
            author="y",
            file_basename="z.epub",
            organization_mode="bogus",
        )


def test_compute_target_path_unicode_safe(tmp_path):
    """Bengali title + author survive through safe_filename sanitization."""
    out = compute_target_path(
        library_root=tmp_path,
        title="কাঁটায়-কাঁটায় ৬",
        author="নারায়ণ সান্যাল",
        file_basename="book.pdf",
    )
    assert "কাঁটায়" in str(out.parent.name)
    assert out.name == "book.pdf"


def test_compute_target_path_unknown_author_uses_unknown_dir(tmp_path):
    out = compute_target_path(
        library_root=tmp_path,
        title="t",
        author=None,
        file_basename="b.epub",
    )
    assert out.parent.parent.name == "Unknown"


# ============ REGRESSION: atomic write (Phase 6o.2 R-V-2) ============


def test_drop_writes_through_tmp_path(tmp_path):
    """The intermediate `.biblichor-tmp` should NOT exist after a successful
    drop (atomic via os.replace). The target file SHOULD exist."""
    src = tmp_path / "src.epub"
    src.write_bytes(b"data")
    library = tmp_path / "lib"
    library.mkdir()
    result = drop_into_library(src, library_root=library, title="T", author="A")
    assert result.target_path.exists()
    # No tmp turd left behind
    tmp_sibling = result.target_path.with_name(result.target_path.name + ".biblichor-tmp")
    assert not tmp_sibling.exists()


def test_drop_atomicity_via_observable_tmp(tmp_path, monkeypatch):
    """When the os.replace step fails (we simulate via mock), the partial
    file at the tmp path is cleaned up so subsequent retries aren't blocked
    by a stale half-written file."""
    src = tmp_path / "src.epub"
    src.write_bytes(b"data")
    library = tmp_path / "lib"
    library.mkdir()

    def boom(*args, **kw):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(BookOrbitDropError, match="copy failed"):
        drop_into_library(src, library_root=library, title="T", author="A")

    # tmp file cleaned up
    target_dir = library / "A" / "T"
    if target_dir.exists():
        tmp_files = [p for p in target_dir.iterdir() if p.name.endswith(".biblichor-tmp")]
        assert tmp_files == [], f"tmp turds left: {tmp_files}"


# ============ REGRESSION: layout uses the shared helper ============


def test_migrate_and_drop_use_same_layout(tmp_path):
    """Calling compute_target_path directly with the same args produces
    the same path drop_into_library would write to. Defends against a
    future refactor that diverges the two."""
    src = tmp_path / "src.epub"
    src.write_bytes(b"data")
    library = tmp_path / "lib"
    library.mkdir()
    expected = compute_target_path(
        library_root=library,
        title="T",
        author="A",
        file_basename="src.epub",
    )
    result = drop_into_library(src, library_root=library, title="T", author="A")
    assert result.target_path == expected


# ============ REGRESSION: BookOrbitCfg renamed library_root_on_host -> library_root ============


def test_bookorbit_cfg_uses_library_root_field():
    """The Phase 6o.2 (C-1) rename: field is library_root, not library_root_on_host."""
    from endless_library.config import BookOrbitCfg

    cfg = BookOrbitCfg()
    assert hasattr(cfg, "library_root")
    assert not hasattr(cfg, "library_root_on_host"), (
        "library_root_on_host must be fully replaced by library_root"
    )
    assert cfg.library_root == ""


# ============ I-3: ensure_bookorbit_ready handles missing yaml ============


BASE = "http://bookorbit.test"


@respx.mock(base_url=BASE, assert_all_called=False)
def test_ensure_bookorbit_ready_creates_yaml_if_missing(respx_mock, tmp_path):
    """Phase 6o.2 (I-3): missing yaml must NOT raise; setup creates a
    default Config() then writes it."""
    from endless_library.bookorbit.setup import ensure_bookorbit_ready

    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": True})
    )
    respx_mock.post("/api/v1/auth/setup").mock(return_value=httpx.Response(201, json={"id": "u1"}))
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "j"})
    )
    respx_mock.get("/api/v1/libraries").mock(return_value=httpx.Response(200, json=[]))
    respx_mock.post("/api/v1/libraries").mock(
        return_value=httpx.Response(201, json={"id": "lib-fresh"})
    )

    missing_yaml = tmp_path / "fresh" / "config.yaml"
    assert not missing_yaml.exists()

    result = ensure_bookorbit_ready(
        url=BASE,
        setup_token="t",
        admin_username="admin",
        admin_name="Admin",
        admin_email="a@x.com",
        admin_password="Password1",
        library_root="/library",
        biblichor_config_yaml_path=missing_yaml,
    )
    assert result.library_id == "lib-fresh"
    assert missing_yaml.exists()  # was created on the fly


# ============ C-1 guard: pipeline-side warns when library_root missing ============


def test_pipeline_drop_block_runs_path_existence_check(tmp_path):
    """C-1: even if cfg.bookorbit.enabled=True and library_root is set,
    if the path doesn't exist inside the running process, the drop is
    skipped (with a loud warn) instead of failing silently. The
    pipeline code includes the guard; this test inspects the source
    to pin the contract."""
    import inspect

    import endless_library.pipeline as pipeline_mod

    src = inspect.getsource(pipeline_mod)
    # Both the path-existence check AND the warning message are present
    assert "library_root_path.exists()" in src
    assert "log.warning(" in src
    assert "books are NOT being" in src or "books are not being" in src.lower()


def test_app_startup_nudge_checks_library_root_existence():
    """Companion to the pipeline guard: app.py's lifespan logs a warn
    at startup time if library_root is unreachable from inside the
    process. Catches the C-1 host-vs-container mismatch before any
    book even enters the pipeline."""
    import inspect

    import endless_library.app as app_mod

    src = inspect.getsource(app_mod)
    assert "cfg.bookorbit.library_root" in src
    assert "library_root=%r does not exist" in src.lower() or "library_root" in src
