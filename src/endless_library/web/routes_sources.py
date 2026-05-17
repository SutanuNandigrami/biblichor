from __future__ import annotations

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse


def register(app: FastAPI) -> None:
    @app.get("/sources", response_class=HTMLResponse)
    def sources_page(request: Request):
        deps = request.app.state.deps
        templates = request.app.state.templates
        rows = deps.sources.list_all()
        return templates.TemplateResponse(
            request, "sources.html", {"request": request, "sources": rows}
        )

    @app.post("/api/sources")
    def add_source(
        request: Request,
        source: str = Form(...),
        identifier: str = Form(...),
        token: str = Form(""),
    ):
        deps = request.app.state.deps
        if source not in ("goodreads", "hardcover", "manual"):
            raise HTTPException(400, "unknown source")
        try:
            deps.sources.add(source=source, identifier=identifier, token=token or None)
        except Exception as e:
            raise HTTPException(400, str(e)) from e
        return RedirectResponse("/sources", status_code=303)

    @app.post("/api/sources/{sid}/poll")
    def poll_one(sid: int, request: Request):
        from endless_library.pipeline import poll_sources

        deps = request.app.state.deps
        # Quick + dirty: poll all sources (cheap), but we just want this one to refresh
        added = poll_sources(deps)
        return {"ok": True, "added": added}

    @app.post("/api/sources/{sid}/toggle")
    def toggle(sid: int, request: Request):
        deps = request.app.state.deps
        row = deps.sources.get(sid)
        if not row:
            raise HTTPException(404)
        deps.sources.set_enabled(sid, not row.enabled)
        return {"ok": True}

    @app.post("/api/sources/{sid}/delete")
    def delete(sid: int, request: Request):
        deps = request.app.state.deps
        deps.sources.delete(sid)
        return {"ok": True}
