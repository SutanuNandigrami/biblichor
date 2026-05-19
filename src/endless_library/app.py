from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from endless_library.config import Config, load_config
from endless_library.pipeline import PipelineDeps
from endless_library.scheduler import build_scheduler_with_deps
from endless_library.web import api

log = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """Start the APScheduler inside FastAPI's event loop so the cron-like
    poll/process/retry/summary jobs actually run in the same process as the
    web server. Without this, only manual /api/run kicks ever fired.
    """
    sched = build_scheduler_with_deps(app.state.cfg, app.state.deps)
    sched.start()
    app.state.scheduler = sched
    log.info("scheduler started with %d jobs", len(sched.get_jobs()))
    try:
        yield
    finally:
        log.info("scheduler shutting down")
        sched.shutdown(wait=False)


def create_app(*, cfg: Config, deps: PipelineDeps, config_path: Path) -> FastAPI:
    app = FastAPI(
        title="endless-library",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=_lifespan,
    )
    app.state.cfg = cfg
    app.state.deps = deps
    app.state.config_path = config_path
    api.register(app)

    dist = Path(__file__).resolve().parent.parent.parent / "webapp" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="spa-assets")

        @app.get("/{path:path}")
        def spa(path: str):
            return FileResponse(dist / "index.html")

    return app


def entry() -> FastAPI:
    """uvicorn factory entry point."""
    config_path = Path(os.environ.get("CONFIG_PATH", "config/config.yaml"))
    db_path = Path(os.environ.get("LIBRARY_DB", "data/library.db"))
    cfg: Config = load_config(config_path)
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    return create_app(cfg=cfg, deps=deps, config_path=config_path)
