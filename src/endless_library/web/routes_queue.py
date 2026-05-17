from __future__ import annotations

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


def register(app: FastAPI) -> None:
    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(request: Request, status: str | None = None, q: str | None = None):
        deps = request.app.state.deps
        templates = request.app.state.templates
        deps.books.pending(max_attempts=10_000)
        # Also include terminal rows: pull all by re-querying — for simplicity, get pending
        # and supplement with sent/skipped via a direct query
        from endless_library.db.schema import connect

        with connect(deps.db_path) as conn:
            cursor = conn.execute("SELECT * FROM books ORDER BY created_at DESC LIMIT 500")
            from endless_library.db.books import BookRow

            all_rows = [BookRow.from_row(r) for r in cursor.fetchall()]
        if status:
            all_rows = [r for r in all_rows if r.status == status]
        if q:
            qlc = q.lower()
            all_rows = [
                r
                for r in all_rows
                if qlc in (r.title or "").lower() or qlc in (r.author or "").lower()
            ]
        statuses = sorted({r.status for r in all_rows})
        return templates.TemplateResponse(
            request,
            "queue.html",
            {
                "request": request,
                "books": all_rows,
                "status_filter": status,
                "q": q,
                "statuses": statuses,
            },
        )

    @app.post("/api/books/add")
    def add_book(
        request: Request,
        title: str = Form(...),
        author: str = Form(""),
        isbn13: str = Form(""),
    ):
        deps = request.app.state.deps
        bid = deps.books.upsert(
            title=title.strip(),
            author=author.strip() or None,
            isbn13=isbn13.strip() or None,
            source="manual",
            source_id=f"manual:{title.strip().lower()}:{author.strip().lower()}",
        )
        deps.events.append(book_id=bid, kind="state_change", message="added manually via dashboard")
        return RedirectResponse(f"/book/{bid}", status_code=303)

    @app.post("/api/books/{book_id}/retry")
    def retry(book_id: int, request: Request):
        deps = request.app.state.deps
        row = deps.books.get(book_id)
        if not row:
            raise HTTPException(status_code=404)
        deps.books.set_status(book_id, "queued", error=None)
        deps.events.append(
            book_id=book_id, kind="state_change", message="manually re-queued from dashboard"
        )
        return {"ok": True}

    @app.post("/api/books/{book_id}/delete")
    def soft_delete(book_id: int, request: Request):
        deps = request.app.state.deps
        # Mark as skipped — keeps the audit trail
        row = deps.books.get(book_id)
        if not row:
            raise HTTPException(status_code=404)
        deps.books.set_status(book_id, "skipped", error="deleted from dashboard")
        return {"ok": True}

    @app.post("/api/cycle/run-now")
    async def run_now(request: Request):
        import asyncio

        from endless_library.pipeline import process_queue

        deps = request.app.state.deps
        # Fire-and-forget; in production APScheduler max_instances=1 prevents overlap
        state = request.app.state
        if getattr(state, "_running", False):
            return JSONResponse({"reason": "already running"}, status_code=409)
        state._running = True
        try:
            tally = await asyncio.to_thread(process_queue, deps)
        finally:
            state._running = False
        return {"ok": True, "tally": tally}
