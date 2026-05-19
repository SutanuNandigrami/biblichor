"""biblichor bookorbit-doctor — health + DTO-drift checks for BookOrbit.

Run before/after any BookOrbit image bump to catch upstream changes
in the small REST surface biblichor depends on. Phase 6o.8 (R-M-5).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class DoctorReport:
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(CheckResult(name=name, ok=ok, detail=detail))

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _probe_get(client: httpx.Client, path: str, timeout: float = 5.0) -> httpx.Response | None:
    try:
        return client.get(path, timeout=timeout)
    except (httpx.HTTPError, OSError) as e:
        log.debug("probe GET %s failed: %s", path, e)
        return None


def run_doctor(
    *,
    bookorbit_url: str,
    library_root: Path | None,
    library_id: str | None,
    admin_username: str = "",
    admin_password: str = "",
) -> DoctorReport:
    """Run every check biblichor cares about. Doesn't raise on failure;
    every issue is reported as a CheckResult so the CLI can print a
    full summary."""
    report = DoctorReport()
    base = bookorbit_url.rstrip("/")

    with httpx.Client(base_url=base) as client:
        # 1. Health endpoint reachable + DB up
        r = _probe_get(client, "/api/v1/health")
        if r is None:
            report.add("health.reachable", False, f"GET {base}/api/v1/health failed to connect")
        elif r.status_code != 200:
            report.add("health.reachable", False, f"GET /api/v1/health returned {r.status_code}")
        else:
            report.add("health.reachable", True, "200 OK")
            try:
                body = r.json()
                db_up = body.get("info", {}).get("database", {}).get("status") == "up"
                report.add(
                    "health.database",
                    db_up,
                    "database.status=up" if db_up else f"unexpected: {body}",
                )
            except (json.JSONDecodeError, KeyError):
                report.add("health.database", False, "could not parse /health response")

        # 2. setup-status shape
        r = _probe_get(client, "/api/v1/auth/setup-status")
        if r is None or r.status_code != 200:
            report.add(
                "setup_status.reachable",
                False,
                f"GET /auth/setup-status returned {r.status_code if r else 'NO RESPONSE'}",
            )
        else:
            try:
                body = r.json()
                has_key = "needsSetup" in body or "needs_setup" in body
                report.add(
                    "setup_status.dto",
                    has_key,
                    "needsSetup key present" if has_key else f"DTO drift: {body}",
                )
            except json.JSONDecodeError:
                report.add("setup_status.dto", False, "non-JSON response")

        # 3. Library_root exists inside THIS process
        if library_root is None:
            report.add("library_root.configured", False, "cfg.bookorbit.library_root is empty")
        elif not library_root.exists():
            report.add(
                "library_root.exists",
                False,
                f"{library_root} does not exist in this process (C-1 mismatch)",
            )
        else:
            report.add("library_root.exists", True, str(library_root))

        # 4. Authenticated checks (only if creds provided)
        if admin_username and admin_password:
            login_r = client.post(
                "/api/v1/auth/login",
                json={"username": admin_username, "password": admin_password},
                timeout=10.0,
            )
            if login_r.status_code != 200:
                report.add(
                    "auth.login",
                    False,
                    f"login failed: {login_r.status_code} {login_r.text[:200]}",
                )
            else:
                report.add("auth.login", True, "200 OK")
                token = login_r.json().get("accessToken") or login_r.json().get("access_token")
                if token:
                    auth_h = {"Authorization": f"Bearer {token}"}
                    # 5. /libraries shape
                    libs_r = client.get("/api/v1/libraries", headers=auth_h)
                    if libs_r.status_code != 200:
                        report.add("libraries.list", False, f"returned {libs_r.status_code}")
                    else:
                        libs = libs_r.json()
                        if not isinstance(libs, list):
                            report.add(
                                "libraries.dto",
                                False,
                                f"expected list, got {type(libs).__name__}",
                            )
                        else:
                            keys_ok = all(
                                isinstance(lib, dict) and "id" in lib and "name" in lib
                                for lib in libs
                            )
                            report.add(
                                "libraries.dto",
                                keys_ok,
                                f"{len(libs)} libraries; expected keys present"
                                if keys_ok
                                else f"DTO drift: {libs[:1]}",
                            )
                            # 6. biblichor library present
                            if library_id is not None:
                                hit = any(str(lib.get("id")) == str(library_id) for lib in libs)
                                report.add(
                                    "libraries.biblichor_present",
                                    hit,
                                    f"id={library_id} found" if hit else f"id={library_id} missing",
                                )
        else:
            report.add(
                "auth.creds_provided",
                True,
                "skipped authenticated checks (no creds passed)",
            )

        # 7. OPDS endpoint responds (with or without auth — 401 is fine, means
        # the endpoint exists and just wants credentials)
        r = _probe_get(client, "/api/v1/opds")
        if r is None:
            report.add("opds.reachable", False, "GET /api/v1/opds failed to connect")
        elif r.status_code in (200, 401, 403):
            report.add(
                "opds.reachable",
                True,
                f"{r.status_code} (401/403 = ok, just unauthenticated)",
            )
        else:
            report.add("opds.reachable", False, f"unexpected {r.status_code}")

    return report
