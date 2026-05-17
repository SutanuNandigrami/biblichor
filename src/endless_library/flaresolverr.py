"""FlareSolverr HTTP client. POSTs to /v1 with a request.get command,
parses the `solution.response` HTML out of the response."""

from __future__ import annotations

import logging
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
        # FlareSolverr can take ~30s for a CF challenge; pad the HTTP timeout
        return httpx.Client(timeout=(self.max_timeout_ms / 1000) + 15)

    def get(self, url: str, *, session: str | None = None) -> FlareSolverrResponse:
        payload = {"cmd": "request.get", "url": url, "maxTimeout": self.max_timeout_ms}
        if session:
            payload["session"] = session
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
