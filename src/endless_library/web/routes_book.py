from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse


def register(app: FastAPI) -> None:
    @app.get("/book/{book_id}", response_class=HTMLResponse)
    def detail(book_id: int, request: Request):
        deps = request.app.state.deps
        templates = request.app.state.templates
        book = deps.books.get(book_id)
        if not book:
            raise HTTPException(404)
        cands = deps.cands.top_for_book(book_id, limit=10)
        events = deps.events.recent_for_book(book_id, limit=200)
        return templates.TemplateResponse(
            request,
            "book.html",
            {"request": request, "book": book, "candidates": cands, "events": events},
        )

    @app.post("/api/books/{book_id}/pick/{cand_id}")
    def pick(book_id: int, cand_id: int, request: Request):
        deps = request.app.state.deps
        from endless_library.db.schema import connect

        with connect(deps.db_path) as conn:
            cur = conn.execute(
                "UPDATE books SET picked_candidate_id=?, status='queued', last_error=NULL, "
                "updated_at=datetime('now') WHERE id=?",
                (cand_id, book_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(404)
        deps.events.append(
            book_id=book_id, kind="state_change", message=f"manually picked candidate {cand_id}"
        )
        return {"ok": True}
