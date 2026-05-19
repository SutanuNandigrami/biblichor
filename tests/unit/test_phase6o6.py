"""Phase 6o.6 tests for auto-scan cron + README guidance."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import httpx
import respx

from endless_library.bookorbit.client import BookOrbitClient

BASE = "http://bookorbit.test"


# ============ REGRESSION: auto-scan cron passed to BookOrbit ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_create_library_passes_auto_scan_cron(respx_mock):
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "j"})
    )
    create_route = respx_mock.post("/api/v1/libraries").mock(
        return_value=httpx.Response(201, json={"id": "lib-1"})
    )
    with BookOrbitClient(BASE) as c:
        c.login(username="admin", password="x")
        c.create_library(
            name="biblichor",
            icon="📚",
            folders=["/library"],
            auto_scan_cron_expression="0 * * * *",
        )
    body = json.loads(create_route.calls.last.request.content)
    assert body["autoScanCronExpression"] == "0 * * * *"


@respx.mock(base_url=BASE, assert_all_called=False)
def test_create_library_skips_cron_when_none(respx_mock):
    """If the caller doesn't pass a cron, the field is omitted from the
    DTO (BookOrbit's default 'no cron' applies)."""
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "j"})
    )
    create_route = respx_mock.post("/api/v1/libraries").mock(
        return_value=httpx.Response(201, json={"id": "lib-1"})
    )
    with BookOrbitClient(BASE) as c:
        c.login(username="admin", password="x")
        c.create_library(name="x", icon="📚", folders=["/library"])
    body = json.loads(create_route.calls.last.request.content)
    assert "autoScanCronExpression" not in body


# ============ REGRESSION: setup uses the hourly cron ============


def test_setup_passes_hourly_cron_on_library_creation():
    """ensure_bookorbit_ready calls create_library with the
    documented hourly cron expression."""
    import endless_library.bookorbit.setup as setup_mod

    src = inspect.getsource(setup_mod)
    assert 'auto_scan_cron_expression="0 * * * *"' in src or (
        "0 * * * *" in src and "auto_scan_cron_expression" in src
    )


# ============ README documents provider opt-in + field-rule warning ============


def test_readme_warns_about_metadata_provider_field_rules():
    readme = (Path(__file__).parent.parent.parent / "README.md").read_text()
    assert "Fill missing only" in readme
    assert "Bengali" in readme  # the protect-Bengali-enrichment guidance
    # Field rules warning specifically mentions Goodreads as a Latin-only source
    assert "Goodreads" in readme


def test_readme_documents_auto_scan_cron():
    readme = (Path(__file__).parent.parent.parent / "README.md").read_text()
    assert "auto-scan cron" in readme
    assert "0 * * * *" in readme or "hourly" in readme
