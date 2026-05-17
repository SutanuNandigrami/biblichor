from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import FastAPI, Request


def register(app: FastAPI) -> None:
    @app.get("/healthz")
    def healthz(request: Request):
        deps = request.app.state.deps
        queue = len(deps.books.pending(max_attempts=10_000))
        db_size = 0
        with contextlib.suppress(FileNotFoundError):
            db_size = Path(deps.db_path).stat().st_size
        return {
            "ok": True,
            "queue_size": queue,
            "db_size_bytes": db_size,
        }
