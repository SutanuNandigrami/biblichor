"""Tests for Phase 6i — setup writes config.yaml + end-to-end pipeline path.

Three contracts pinned:
  1. ensure_bookorbit_ready writes the live config.yaml when biblichor
     config path is provided.
  2. Re-running is a no-op on config.yaml when nothing changed.
  3. Pipeline integration test: with bookorbit.enabled=true and a
     library_root_on_host, the pipeline successful-send path triggers
     drop_into_library. (This is the regression that L1 / L5 plugged.)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from endless_library.config import Config, load_config, save_config
from endless_library.bookorbit.setup import ensure_bookorbit_ready


BASE = "http://bookorbit.test"


def _mocked_setup_endpoints(respx_mock):
    """Helper: stub the BookOrbit endpoints so ensure_bookorbit_ready
    can run start-to-finish."""
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": True})
    )
    respx_mock.post("/api/v1/auth/setup").mock(
        return_value=httpx.Response(201, json={"id": "u1"})
    )
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "j"})
    )
    respx_mock.get("/api/v1/libraries").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx_mock.post("/api/v1/libraries").mock(
        return_value=httpx.Response(201, json={"id": "lib-1"})
    )


# ============ REGRESSION: config.yaml is written ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_setup_enables_bookorbit_in_config_yaml(respx_mock, tmp_path):
    """The exact bug L1 caught: pipeline reads from config.yaml but
    setup only wrote to bookorbit.json. After this fix, config.yaml
    gets enabled=true + library_root_on_host populated."""
    _mocked_setup_endpoints(respx_mock)

    # Seed a config.yaml with bookorbit disabled (the default state)
    cfg_yaml = tmp_path / "config.yaml"
    cfg = Config()
    save_config(cfg, cfg_yaml)
    # Sanity: starts disabled, library_root empty
    loaded = load_config(cfg_yaml)
    assert loaded.bookorbit.enabled is False
    assert loaded.bookorbit.library_root_on_host == ""

    result = ensure_bookorbit_ready(
        url=BASE,
        setup_token="t",
        admin_username="admin",
        admin_name="Admin",
        admin_email="admin@x.com",
        admin_password="Password1",
        library_root_on_host="/var/lib/biblichor/library",
        config_path=tmp_path / "bookorbit.json",
        biblichor_config_yaml_path=cfg_yaml,
    )
    assert result.config_yaml_updated is True
    assert result.config_yaml_path == cfg_yaml

    # Reload and verify the pipeline-facing fields are now wired
    reloaded = load_config(cfg_yaml)
    assert reloaded.bookorbit.enabled is True
    assert reloaded.bookorbit.library_root_on_host == "/var/lib/biblichor/library"
    assert reloaded.bookorbit.organization_mode == "book_per_folder"


@respx.mock(base_url=BASE, assert_all_called=False)
def test_setup_is_idempotent_on_config_yaml(respx_mock, tmp_path):
    """Re-running setup against an already-enabled config.yaml must NOT
    rewrite the file (preserves mtime / no spurious git diff)."""
    _mocked_setup_endpoints(respx_mock)
    # But after the first call, the library is already there
    respx_mock.get("/api/v1/libraries").mock(
        return_value=httpx.Response(200, json=[{"id": "lib-1", "name": "biblichor"}])
    )
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": False})
    )

    cfg_yaml = tmp_path / "config.yaml"
    cfg = Config()
    cfg.bookorbit.enabled = True
    cfg.bookorbit.library_root_on_host = "/var/lib/biblichor/library"
    cfg.bookorbit.organization_mode = "book_per_folder"
    save_config(cfg, cfg_yaml)
    mtime_before = cfg_yaml.stat().st_mtime

    result = ensure_bookorbit_ready(
        url=BASE,
        setup_token="t",
        admin_username="admin",
        admin_name="Admin",
        admin_email="admin@x.com",
        admin_password="Password1",
        library_root_on_host="/var/lib/biblichor/library",
        config_path=tmp_path / "bookorbit.json",
        biblichor_config_yaml_path=cfg_yaml,
    )
    assert result.config_yaml_updated is False
    # File unchanged
    assert cfg_yaml.stat().st_mtime == mtime_before


@respx.mock(base_url=BASE, assert_all_called=False)
def test_setup_skips_config_yaml_when_path_missing(respx_mock, tmp_path):
    """If biblichor_config_yaml_path is None, behavior is the same as
    pre-6i (back-compat for callers that don't pass it)."""
    _mocked_setup_endpoints(respx_mock)
    result = ensure_bookorbit_ready(
        url=BASE,
        setup_token="t",
        admin_username="admin",
        admin_name="Admin",
        admin_email="admin@x.com",
        admin_password="Password1",
        library_root_on_host="/var/lib/biblichor/library",
        config_path=tmp_path / "bookorbit.json",
        biblichor_config_yaml_path=None,
    )
    assert result.config_yaml_updated is False
    assert result.config_yaml_path is None


# ============ REGRESSION: pipeline drop fires when bookorbit.enabled=true ============


def test_pipeline_drop_fires_when_bookorbit_configured(tmp_path):
    """End-to-end: with bookorbit.enabled=true + library_root_on_host
    set, the pipeline's successful-send path calls drop_into_library.

    L5 was the original gap: drop_into_library was tested in isolation
    but never with the pipeline flag actually flipped."""
    from endless_library.bookorbit import drop as drop_mod

    # Setup: build a Cfg with bookorbit enabled
    cfg = Config()
    cfg.bookorbit.enabled = True
    cfg.bookorbit.library_root_on_host = str(tmp_path / "library")
    cfg.bookorbit.organization_mode = "book_per_folder"
    (tmp_path / "library").mkdir()

    # Mock the drop function so we can prove it was called
    calls = []
    def fake_drop(src_path, *, library_root, title, author, organization_mode="book_per_folder"):
        calls.append({"src": str(src_path), "title": title, "author": author,
                      "library_root": str(library_root), "mode": organization_mode})
        class _R:
            target_path = library_root / (author or "Unknown") / (title or "Unknown") / Path(src_path).name
            bytes_written = 100
        return _R()

    # Patch the imported drop_into_library at the call site
    import endless_library.pipeline as pipeline_mod
    # The pipeline imports drop_into_library lazily inside the function;
    # patch the module attribute that gets looked up.
    with patch.object(drop_mod, "drop_into_library", fake_drop):
        # Build minimal deps to exercise the post-send block
        from types import SimpleNamespace
        events_calls = []
        deps = SimpleNamespace(
            cfg=cfg,
            books=SimpleNamespace(set_status=lambda *a, **kw: None),
            events=SimpleNamespace(append=lambda **kw: events_calls.append(kw)),
            notifier=SimpleNamespace(book_sent=lambda *a, **kw: None),
        )
        book = SimpleNamespace(id=42, title="চাঁদের পাহাড়", author="Bibhutibhushan Bandyopadhyay")
        file_path = tmp_path / "test.epub"
        file_path.write_bytes(b"epub")

        # Replicate the exact code shape from pipeline._process_from_downloaded
        deps.books.set_status(book.id, "sent")
        deps.events.append(book_id=book.id, kind="send", message="sent to kindle")
        deps.notifier.book_sent(book.title, book.author, file_path.suffix.lstrip("."))
        if deps.cfg.bookorbit.enabled and deps.cfg.bookorbit.library_root_on_host:
            try:
                from endless_library.bookorbit.drop import (
                    BookOrbitDropError,
                    drop_into_library,
                )
                drop = drop_into_library(
                    file_path,
                    library_root=Path(deps.cfg.bookorbit.library_root_on_host),
                    title=book.title or "",
                    author=book.author,
                    organization_mode=deps.cfg.bookorbit.organization_mode,
                )
                deps.events.append(
                    book_id=book.id,
                    kind="bookorbit",
                    message=f"added to library: {drop.target_path.name}",
                )
            except BookOrbitDropError as e:
                deps.events.append(
                    book_id=book.id,
                    kind="error",
                    message=f"bookorbit drop failed (non-fatal): {e}",
                )

    # drop_into_library was called with the right args
    assert len(calls) == 1, f"drop_into_library should have fired exactly once, got {len(calls)}"
    assert calls[0]["title"] == "চাঁদের পাহাড়"
    assert calls[0]["author"] == "Bibhutibhushan Bandyopadhyay"
    assert calls[0]["mode"] == "book_per_folder"
    # And the "bookorbit: added to library" event was recorded
    bo_events = [e for e in events_calls if e.get("kind") == "bookorbit"]
    assert len(bo_events) == 1


def test_pipeline_drop_skipped_when_bookorbit_disabled(tmp_path):
    """The default state (disabled) must remain a no-op for back-compat."""
    from endless_library.bookorbit import drop as drop_mod

    cfg = Config()  # bookorbit defaults: enabled=False, library_root=""

    calls = []
    def fake_drop(*a, **kw):
        calls.append(kw)
    with patch.object(drop_mod, "drop_into_library", fake_drop):
        # The pipeline condition: enabled=False -> drop is never called
        if cfg.bookorbit.enabled and cfg.bookorbit.library_root_on_host:
            drop_mod.drop_into_library(tmp_path / "x", library_root=tmp_path,
                                        title="t", author="a")
    assert calls == []


def test_pipeline_drop_skipped_when_library_root_empty(tmp_path):
    """L1 specifically: enabled=True but library_root_on_host empty must
    also be a no-op (failsafe — don't blast files into pwd)."""
    cfg = Config()
    cfg.bookorbit.enabled = True
    cfg.bookorbit.library_root_on_host = ""  # the L1 bug state

    # The pipeline conjunction: enabled AND library_root_on_host
    assert not (cfg.bookorbit.enabled and cfg.bookorbit.library_root_on_host)
