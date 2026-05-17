"""Reverse-proxy /library/* to the Calibre-Web container on 127.0.0.1:8083.

Why: Calibre-Web sends X-Frame-Options: SAMEORIGIN. Iframing it from a
different origin (port 8090) is blocked. Proxying it through us makes both
UIs same-origin, so the SPA can embed it via iframe.

What's rewritten:
  * absolute-path URLs in HTML (`href="/..."`, `src="/..."`) get the /library
    prefix so the browser loads them through us, not directly against 8083.
  * Set-Cookie headers have Path rewritten the same way.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import FastAPI, Request, Response

log = logging.getLogger(__name__)

INTERNAL_CALIBRE = "http://127.0.0.1:8083"
PROXY_PREFIX = "/library"

# Hop-by-hop headers that must never be forwarded (RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}

_HTML_REWRITES = (
    # href="/foo" / src="/foo" / action="/foo"  — must NOT match //example.com
    (
        re.compile(rb'(\s(?:href|src|action|data-url)=["\'])/(?![/])'),
        rb"\1" + PROXY_PREFIX.encode() + b"/",
    ),
    # url(/foo)  inside CSS / style attributes
    (re.compile(rb"(url\((['\"]?))/(?![/])"), rb"\1\2" + PROXY_PREFIX.encode() + b"/"),
    # absolute-path location in JS (best-effort; not exhaustive)
    (re.compile(rb"(location\.href\s*=\s*['\"])/(?![/])"), rb"\1" + PROXY_PREFIX.encode() + b"/"),
)


def _rewrite_html(body: bytes) -> bytes:
    for patt, repl in _HTML_REWRITES:
        body = patt.sub(repl, body)
    return body


def _rewrite_cookie_path(set_cookie: str) -> str:
    """Cookie path / should become /library/ so the browser sends them back."""
    return re.sub(r"(?i)(\bPath=)/", rf"\1{PROXY_PREFIX}/", set_cookie)


def register(app: FastAPI) -> None:
    @app.api_route(
        "/library/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def proxy(path: str, request: Request) -> Response:
        target = f"{INTERNAL_CALIBRE}/{path}"

        # Forward headers, dropping hop-by-hop + the Host (which would point at us)
        fwd_headers: dict[str, str] = {}
        for k, v in request.headers.items():
            if k.lower() not in _HOP_BY_HOP:
                fwd_headers[k] = v
        # Note our presence + the SPA's origin for upstream-side awareness
        if "x-forwarded-for" not in fwd_headers:
            fwd_headers["x-forwarded-for"] = request.client.host if request.client else ""
        fwd_headers["x-forwarded-proto"] = request.url.scheme
        fwd_headers["x-forwarded-prefix"] = PROXY_PREFIX
        body = await request.body()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, read=120.0),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                upstream = await client.request(
                    request.method,
                    target,
                    headers=fwd_headers,
                    params=str(request.url.query) or None,
                    content=body,
                )
        except httpx.RequestError as e:
            log.warning("library proxy failed %s: %s", target, e)
            return Response(
                content=f"Calibre-Web upstream unreachable: {e}".encode(),
                status_code=502,
                media_type="text/plain",
            )

        # ---- response headers ----
        out_headers: list[tuple[str, str]] = []
        for k, v in upstream.headers.multi_items():
            kl = k.lower()
            if kl in _HOP_BY_HOP:
                continue
            if kl in {"content-length", "content-encoding"}:
                # Drop — we may rewrite body and httpx may have decoded gzip already
                continue
            if kl == "location":
                # Rewrite absolute-path redirects: /login → /library/login
                if v.startswith("/") and not v.startswith("//"):
                    v = PROXY_PREFIX + v
                out_headers.append((k, v))
                continue
            if kl == "set-cookie":
                v = _rewrite_cookie_path(v)
                out_headers.append((k, v))
                continue
            out_headers.append((k, v))

        # ---- body (rewrite HTML/CSS, pass binary through unchanged) ----
        content_type = upstream.headers.get("content-type", "")
        body_out = upstream.content
        if any(t in content_type.lower() for t in ("text/html", "text/css")):
            body_out = _rewrite_html(body_out)

        return Response(
            content=body_out,
            status_code=upstream.status_code,
            headers=dict(out_headers),
            media_type=content_type or None,
        )

    @app.get("/library")
    async def library_root(request: Request) -> Response:
        # Convenience: /library (no trailing slash) → /library/
        return Response(status_code=302, headers={"Location": PROXY_PREFIX + "/"})
