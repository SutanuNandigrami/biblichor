from __future__ import annotations

import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse


@dataclass
class Checks:
    sources: bool
    sources_count: int
    smtp: bool
    kindle: bool
    amazon_whitelist_hint: bool
    calibre: bool
    calibre_version: str
    outbound_smtp: bool | None
    smtp_probe: str | None


def _probe_smtp(host: str, port: int, timeout: float = 5.0) -> str:
    """Returns 'OK <host>:<port>' or 'FAIL ...'."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return f"OK {host}:{port}"
    except Exception as e:
        return f"FAIL {host}:{port} ({e})"


def _gather(deps) -> Checks:
    cfg = deps.cfg
    sources = deps.sources.list_enabled()
    calibre_bin = shutil.which("ebook-convert")
    calibre_ver = ""
    if calibre_bin:
        try:
            r = subprocess.run(
                [calibre_bin, "--version"], capture_output=True, text=True, timeout=5
            )
            calibre_ver = (r.stdout or r.stderr).strip().splitlines()[0][:80]
        except Exception:
            calibre_ver = "(present, version probe failed)"
    smtp_ok = bool(cfg.smtp.host and cfg.smtp.user and cfg.smtp.password)
    return Checks(
        sources=len(sources) > 0,
        sources_count=len(sources),
        smtp=smtp_ok,
        kindle=bool(cfg.kindle.recipient),
        amazon_whitelist_hint=bool(cfg.smtp.user),
        calibre=bool(calibre_bin),
        calibre_version=calibre_ver,
        outbound_smtp=None,
        smtp_probe=getattr(deps, "_last_smtp_probe", None),
    )


def register(app: FastAPI) -> None:
    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request):
        deps = request.app.state.deps
        templates = request.app.state.templates
        checks = _gather(deps)
        return templates.TemplateResponse(
            request, "setup.html", {"cfg": deps.cfg.public_view(), "checks": asdict(checks)}
        )

    @app.post("/api/setup/probe-smtp")
    def probe_smtp(request: Request):
        deps = request.app.state.deps
        cfg = deps.cfg
        host = cfg.smtp.host or "smtp.gmail.com"
        port = int(cfg.smtp.port or 587)
        result = _probe_smtp(host, port)
        deps._last_smtp_probe = result  # type: ignore[attr-defined]
        return {"ok": result.startswith("OK"), "result": result}
