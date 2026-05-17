"""Reverse-proxy /library/* to the Calibre-Web container on 127.0.0.1:8083.

Why: Calibre-Web sends X-Frame-Options: SAMEORIGIN. Iframing it from a
different origin (port 8090) is blocked. Proxying it through us makes both
UIs same-origin, so the SPA can embed it via iframe.

What's rewritten:
  * absolute-path URLs in HTML attributes (`href="/..."`, `src="/..."`, ...)
  * `srcset` values (browser prefers these on retina displays)
  * JS string literals referencing Calibre-Web endpoints (the reader builds
    its book URL in JS: `bookUrl: "/show/3/epub/file.epub"`)
  * Set-Cookie path
  * Redirect Location header

We also send `X-Script-Name: /library` upstream so Calibre-Web's Flask
`url_for` natively generates prefixed URLs when its reverse-proxy support is
enabled in admin config.
"""

from __future__ import annotations

import logging
import re

import httpx
from fastapi import FastAPI, Request, Response

log = logging.getLogger(__name__)

INTERNAL_CALIBRE = "http://127.0.0.1:8083"
PROXY_PREFIX = "/library"

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

_PREFIX_B = PROXY_PREFIX.encode()

# Standard attribute rewrites: href/src/action/data-url ="/foo"
_ATTR_RE = re.compile(rb'(\s(?:href|src|action|data-url|poster)=["\'])/(?![/])')

# srcset has comma-separated URLs each with a descriptor
_SRCSET_RE = re.compile(rb'(\ssrcset=["\'])([^"\']+)(["\'])')

# CSS url(/foo)
_CSS_URL_RE = re.compile(rb"(url\((['\"]?))/(?![/])")

# JS string literals referencing Calibre-Web endpoints. Restricted to the
# specific paths Calibre-Web generates in script blocks (reader bookUrl,
# AJAX endpoints, OPDS feed etc.) to avoid false-positives on unrelated data.
_JS_ENDPOINTS = (
    "show",
    "read",
    "ajax",
    "cover",
    "get",
    "extract",
    "op",
    "feed",
    "serve",
    "me",
    "stats",
    "page",
    "kobo",
    "book",
    "author",
    "series",
    "publisher",
    "tag",
    "format",
    "language",
    "ratings",
    "search",
    "shelf",
    "list",
)
_JS_STR_RE = re.compile(
    rb"([\x22\x27])/(" + b"|".join(p.encode() for p in _JS_ENDPOINTS) + rb")(/|[\x22\x27])"
)


def _rewrite_srcset(m: re.Match[bytes]) -> bytes:
    inner = m.group(2)
    parts = inner.split(b",")
    fixed = []
    for p in parts:
        p_stripped = p.lstrip()
        leading = p[: len(p) - len(p_stripped)]
        if p_stripped.startswith(b"/") and not p_stripped.startswith(b"//"):
            p_stripped = _PREFIX_B + p_stripped
        fixed.append(leading + p_stripped)
    return m.group(1) + b",".join(fixed) + m.group(3)


def _rewrite_js_str(m: re.Match[bytes]) -> bytes:
    quote, name, tail = m.group(1), m.group(2), m.group(3)
    return quote + _PREFIX_B + b"/" + name + tail


def _rewrite_html(body: bytes) -> bytes:
    body = _ATTR_RE.sub(rb"\1" + _PREFIX_B + b"/", body)
    body = _SRCSET_RE.sub(_rewrite_srcset, body)
    body = _CSS_URL_RE.sub(rb"\1\2" + _PREFIX_B + b"/", body)
    body = _JS_STR_RE.sub(_rewrite_js_str, body)
    return body


def _rewrite_cookie_path(set_cookie: str) -> str:
    return re.sub(r"(?i)(\bPath=)/", rf"\1{PROXY_PREFIX}/", set_cookie)


def register(app: FastAPI) -> None:
    @app.api_route(
        "/library/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def proxy(path: str, request: Request) -> Response:
        target = f"{INTERNAL_CALIBRE}/{path}"

        fwd_headers: dict[str, str] = {}
        for k, v in request.headers.items():
            if k.lower() not in _HOP_BY_HOP:
                fwd_headers[k] = v
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

        out_headers: list[tuple[str, str]] = []
        for k, v in upstream.headers.multi_items():
            kl = k.lower()
            if kl in _HOP_BY_HOP:
                continue
            if kl in {"content-length", "content-encoding"}:
                continue
            if kl == "location":
                if v.startswith("/") and not v.startswith("//"):
                    v = PROXY_PREFIX + v
                out_headers.append((k, v))
                continue
            if kl == "set-cookie":
                v = _rewrite_cookie_path(v)
                out_headers.append((k, v))
                continue
            out_headers.append((k, v))

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
        return Response(status_code=302, headers={"Location": PROXY_PREFIX + "/"})
