"""Phase 6o.8 tests for bookorbit-doctor + README polish."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from endless_library.bookorbit.doctor import run_doctor


def _docs_text() -> str:
    """Phase 6q: docs moved from README to docs/wiki/. Search both."""
    root = Path(__file__).parent.parent.parent
    parts = [(root / "README.md").read_text()]
    wiki = root / "docs" / "wiki"
    if wiki.exists():
        for f in sorted(wiki.glob("*.md")):
            parts.append(f.read_text())
    return chr(10).join(parts)




BASE = "http://bookorbit.test"


# ============ doctor checks ============


@respx.mock(base_url=BASE, assert_all_called=False)
def test_doctor_reports_ok_on_healthy_stack(respx_mock, tmp_path):
    respx_mock.get("/api/v1/health").mock(
        return_value=httpx.Response(200, json={"info": {"database": {"status": "up"}}})
    )
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": False})
    )
    respx_mock.get("/api/v1/opds").mock(return_value=httpx.Response(401))  # 401 = endpoint exists

    library_root = tmp_path / "library"
    library_root.mkdir()

    report = run_doctor(
        bookorbit_url=BASE,
        library_root=library_root,
        library_id=None,
    )
    assert report.ok, f"failed checks: {[c for c in report.checks if not c.ok]}"


@respx.mock(base_url=BASE, assert_all_called=False)
def test_doctor_flags_unreachable_bookorbit(respx_mock, tmp_path):
    respx_mock.get("/api/v1/health").mock(side_effect=httpx.ConnectError("refused"))
    respx_mock.get("/api/v1/auth/setup-status").mock(side_effect=httpx.ConnectError("refused"))
    respx_mock.get("/api/v1/opds").mock(side_effect=httpx.ConnectError("refused"))

    report = run_doctor(
        bookorbit_url=BASE,
        library_root=tmp_path,
        library_id=None,
    )
    assert not report.ok
    health_check = next(c for c in report.checks if c.name == "health.reachable")
    assert not health_check.ok


@respx.mock(base_url=BASE, assert_all_called=False)
def test_doctor_flags_missing_library_root(respx_mock, tmp_path):
    """The C-1 contract — doctor surfaces library_root path mismatches."""
    respx_mock.get("/api/v1/health").mock(
        return_value=httpx.Response(200, json={"info": {"database": {"status": "up"}}})
    )
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": False})
    )
    respx_mock.get("/api/v1/opds").mock(return_value=httpx.Response(401))

    bogus_path = tmp_path / "does-not-exist"
    report = run_doctor(
        bookorbit_url=BASE,
        library_root=bogus_path,
        library_id=None,
    )
    lr_check = next(c for c in report.checks if c.name == "library_root.exists")
    assert not lr_check.ok
    assert "C-1" in lr_check.detail or "does not exist" in lr_check.detail


@respx.mock(base_url=BASE, assert_all_called=False)
def test_doctor_flags_dto_drift_on_setup_status(respx_mock, tmp_path):
    """If BookOrbit ever renames `needsSetup` -> `requiresInit`, doctor
    must surface it as a fail."""
    respx_mock.get("/api/v1/health").mock(
        return_value=httpx.Response(200, json={"info": {"database": {"status": "up"}}})
    )
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"requiresInit": True})
    )
    respx_mock.get("/api/v1/opds").mock(return_value=httpx.Response(401))

    report = run_doctor(
        bookorbit_url=BASE,
        library_root=tmp_path,
        library_id=None,
    )
    dto_check = next(c for c in report.checks if c.name == "setup_status.dto")
    assert not dto_check.ok


@respx.mock(base_url=BASE, assert_all_called=False)
def test_doctor_auth_checks_when_creds_provided(respx_mock, tmp_path):
    respx_mock.get("/api/v1/health").mock(
        return_value=httpx.Response(200, json={"info": {"database": {"status": "up"}}})
    )
    respx_mock.get("/api/v1/auth/setup-status").mock(
        return_value=httpx.Response(200, json={"needsSetup": False})
    )
    respx_mock.get("/api/v1/opds").mock(return_value=httpx.Response(401))
    respx_mock.post("/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"accessToken": "jwt"})
    )
    respx_mock.get("/api/v1/libraries").mock(
        return_value=httpx.Response(200, json=[{"id": "lib-1", "name": "biblichor"}])
    )

    report = run_doctor(
        bookorbit_url=BASE,
        library_root=tmp_path,
        library_id="lib-1",
        admin_username="admin",
        admin_password="x",
    )
    # Login + library list + biblichor presence all passed
    login_check = next(c for c in report.checks if c.name == "auth.login")
    assert login_check.ok
    dto_check = next(c for c in report.checks if c.name == "libraries.dto")
    assert dto_check.ok
    present_check = next(c for c in report.checks if c.name == "libraries.biblichor_present")
    assert present_check.ok


# ============ README contracts ============


def test_readme_has_puid_pgid_section():
    readme = _docs_text()
    assert "PUID/PGID" in readme or "PUID:PGID" in readme or "PUID" in readme
    assert "UID 1000" in readme or "1000" in readme


def test_readme_documents_image_pinning():
    readme = _docs_text()
    assert "sha256" in readme
    assert "bookorbit-doctor" in readme  # the upgrade-checklist references it


def test_readme_lists_api_endpoints_biblichor_depends_on():
    readme = _docs_text()
    # Each endpoint biblichor calls is in the table
    for endpoint in (
        "/api/v1/health",
        "/api/v1/auth/setup",
        "/api/v1/auth/login",
        "/api/v1/libraries",
        "/api/v1/scanner",
        "/api/v1/opds",
    ):
        assert endpoint in readme, f"{endpoint} missing from API contract table"


def test_readme_cutover_gotchas_updated():
    readme = _docs_text()
    assert "File ownership flip" in readme or "chown" in readme
    assert "BOOKORBIT_* secrets are NOT in your native" in readme or "fresh" in readme