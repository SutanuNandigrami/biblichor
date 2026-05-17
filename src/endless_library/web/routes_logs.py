from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse


def register(app: FastAPI) -> None:
    @app.get("/logs", response_class=HTMLResponse)
    def logs(request: Request, kind: str | None = None):
        deps = request.app.state.deps
        templates = request.app.state.templates
        rows = deps.events.recent_global(limit=500)
        if kind:
            rows = [r for r in rows if r.kind == kind]
        kinds = sorted({r.kind for r in rows})
        return templates.TemplateResponse(
            request,
            "logs.html",
            {"request": request, "events": rows, "kinds": kinds, "kind_filter": kind},
        )
