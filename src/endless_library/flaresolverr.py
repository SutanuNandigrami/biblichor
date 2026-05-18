"""FlareSolverr HTTP client with session support.

Sessions matter for endpoints that depend on cookies set in earlier requests
— e.g. Anna's / Welib `/slow_download/` pages whose countdown is tracked
server-side per cookie. Without a session, every `request.get` launches a
fresh Chromium and the countdown resets each poll, so we never break free.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


class FlareSolverrError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FlareSolverrResponse:
    status_code: int
    text: str
    user_agent: str
    cookies: list[dict]


class FlareSolverr:
    """Thin client wrapping FlareSolverr's /v1 endpoint."""

    def __init__(
        self,
        endpoint: str,
        *,
        max_timeout_ms: int = 60_000,
        client_factory=None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.max_timeout_ms = max_timeout_ms
        self._client_factory = client_factory

    def _factory(self):
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.Client(timeout=(self.max_timeout_ms / 1000) + 15)

    # ---------- session lifecycle ----------

    def create_session(self, session: str | None = None) -> str:
        """Create a FlareSolverr session; returns the session id.

        Sessions keep cookies + the same browser process alive across calls.
        """
        sid = session or f"el-{uuid.uuid4().hex[:12]}"
        with self._factory() as c:
            r = c.post(self.endpoint, json={"cmd": "sessions.create", "session": sid})
            r.raise_for_status()
            body = r.json()
        if body.get("status") != "ok":
            raise FlareSolverrError(
                f"sessions.create failed: {body.get('status')} {body.get('message')}"
            )
        return sid

    def destroy_session(self, session: str) -> None:
        try:
            with self._factory() as c:
                c.post(self.endpoint, json={"cmd": "sessions.destroy", "session": session})
        except Exception as e:
            log.debug("sessions.destroy ignored error: %s", e)

    @contextmanager
    def session(self) -> Iterator[str]:
        """Context manager that creates a session and destroys it on exit."""
        sid = self.create_session()
        try:
            yield sid
        finally:
            self.destroy_session(sid)

    # ---------- request ----------

    def get(
        self,
        url: str,
        *,
        session: str | None = None,
        cookies: list[dict] | None = None,
    ) -> FlareSolverrResponse:
        """`cookies` is a list of {name, value, domain?} dicts merged
        alongside the Cloudflare-clearance cookies FlareSolverr manages
        on its own, so callers can pass user-auth cookies for logged-in
        flows (e.g. welib auth-cookie injection — task #38)."""
        payload: dict = {"cmd": "request.get", "url": url, "maxTimeout": self.max_timeout_ms}
        if session:
            payload["session"] = session
        if cookies:
            payload["cookies"] = cookies
        with self._factory() as c:
            r = c.post(self.endpoint, json=payload)
            r.raise_for_status()
            body = r.json()
        if body.get("status") != "ok":
            raise FlareSolverrError(
                f"FlareSolverr non-ok: {body.get('status')} {body.get('message')}"
            )
        sol = body.get("solution") or {}
        return FlareSolverrResponse(
            status_code=int(sol.get("status") or 0),
            text=sol.get("response") or "",
            user_agent=sol.get("userAgent") or "",
            cookies=sol.get("cookies") or [],
        )
