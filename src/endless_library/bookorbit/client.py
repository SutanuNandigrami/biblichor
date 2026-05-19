"""Minimal HTTP client for BookOrbit's REST API.

Just enough surface to:
  - bootstrap an admin via /auth/setup (one-time)
  - log in + cache JWT
  - create / find the watched library
  - trigger a manual scan

We use httpx (already a biblichor dep). Auth state is the JWT we get
back from /auth/login; we send it as `Authorization: Bearer ...`.

Refresh-token cookie handling is intentionally skipped: the access
token's 15-min lifespan is plenty for biblichor's short-lived
operations (setup, scan trigger). For long-running services we'd
add refresh logic.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0


class BookOrbitError(Exception):
    """Any non-2xx response from the BookOrbit API."""


class BookOrbitClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        verify: bool | str = True,
    ) -> None:
        """`verify`: passed through to httpx. Default True (system CA
        bundle). Set to a path string for a custom CA bundle (private
        CA), or False to disable verification (dev only — Phase 6o.7
        R-M-8). Override via BOOKORBIT_TLS_CA_BUNDLE env: a non-empty
        path is treated as the CA bundle, the literal string 'false'
        disables verification entirely."""
        import os

        env_override = os.environ.get("BOOKORBIT_TLS_CA_BUNDLE", "").strip()
        if env_override:
            verify = False if env_override.lower() in ("false", "no", "0", "off") else env_override
        self.base_url = base_url.rstrip("/")
        self._jwt: str | None = None
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout, verify=verify)

    def __enter__(self) -> BookOrbitClient:
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    # ---------- health ----------

    def health(self) -> bool:
        """GET /api/v1/health -> True if reachable and database "up".
        Used by the SPA status card. Never raises on connection errors."""
        try:
            r = self._client.get("/api/v1/health")
            if r.status_code != 200:
                return False
            payload = r.json()
            db = payload.get("info", {}).get("database", {}).get("status")
            return db == "up"
        except Exception:
            return False

    # ---------- auth ----------

    def setup_status(self) -> dict[str, Any]:
        """GET /auth/setup-status → {needsSetup: bool, ...}.
        Public endpoint, no auth required."""
        r = self._client.get("/api/v1/auth/setup-status")
        if r.status_code != 200:
            raise BookOrbitError(f"setup-status returned {r.status_code}: {r.text[:200]}")
        return r.json()

    def setup_admin(
        self,
        *,
        token: str,
        username: str,
        name: str,
        email: str,
        password: str,
    ) -> None:
        """One-time admin bootstrap via x-setup-token header. After
        this succeeds, the endpoint becomes inaccessible (responds
        with 403/409 on retry) — that's how BookOrbit gates first-run."""
        r = self._client.post(
            "/api/v1/auth/setup",
            headers={"x-setup-token": token},
            json={"username": username, "name": name, "email": email, "password": password},
        )
        if r.status_code not in (200, 201):
            raise BookOrbitError(f"setup failed ({r.status_code}): {r.text[:300]}")

    def login(self, *, username: str, password: str) -> None:
        """POST /auth/login → stashes the access token for subsequent
        authenticated calls. Refresh-token cookie is ignored for now."""
        r = self._client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        if r.status_code != 200:
            raise BookOrbitError(f"login failed ({r.status_code}): {r.text[:300]}")
        data = r.json()
        token = data.get("accessToken") or data.get("access_token")
        if not token:
            raise BookOrbitError(f"login response missing accessToken: {data}")
        self._jwt = token

    def _auth_headers(self) -> dict[str, str]:
        if not self._jwt:
            raise BookOrbitError("not authenticated — call login() first")
        return {"Authorization": f"Bearer {self._jwt}"}

    # ---------- libraries ----------

    def list_libraries(self) -> list[dict[str, Any]]:
        r = self._client.get("/api/v1/libraries", headers=self._auth_headers())
        if r.status_code != 200:
            raise BookOrbitError(f"list libraries ({r.status_code}): {r.text[:200]}")
        return r.json()

    def create_library(
        self,
        *,
        name: str,
        icon: str,
        folders: list[str],
        watch: bool = True,
        organization_mode: str = "book_per_folder",
        auto_scan_cron_expression: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": name,
            "icon": icon,
            "folders": folders,
            "watch": watch,
            "organizationMode": organization_mode,
        }
        if auto_scan_cron_expression:
            body["autoScanCronExpression"] = auto_scan_cron_expression
        r = self._client.post(
            "/api/v1/libraries",
            headers=self._auth_headers(),
            json=body,
        )
        if r.status_code not in (200, 201):
            raise BookOrbitError(f"create library ({r.status_code}): {r.text[:300]}")
        return r.json()

    def trigger_scan(self, library_id: str) -> None:
        r = self._client.post(
            f"/api/v1/scanner/libraries/{library_id}/scan",
            headers=self._auth_headers(),
        )
        if r.status_code not in (200, 202):
            raise BookOrbitError(f"scan trigger ({r.status_code}): {r.text[:300]}")
