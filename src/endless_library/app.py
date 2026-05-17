from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from endless_library.config import Config, load_config
from endless_library.pipeline import PipelineDeps
from endless_library.web import api


def create_app(*, deps: PipelineDeps, config_path: Path) -> FastAPI:
    app = FastAPI(title="endless-library", docs_url="/api/docs", redoc_url=None)
    app.state.deps = deps
    app.state.config_path = config_path
    api.register(app)

    # Serve SPA static build (webapp/dist) at /. SPA handles routing client-side.
    dist = Path(__file__).resolve().parent.parent.parent / "webapp" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="spa-assets")

        @app.get("/{path:path}")
        def spa(path: str):
            # Any non-/api path returns index.html so vue-router can take over.
            return FileResponse(dist / "index.html")

    return app


def entry() -> FastAPI:
    """uvicorn factory entry point."""
    config_path = Path(os.environ.get("CONFIG_PATH", "config/config.yaml"))
    db_path = Path(os.environ.get("LIBRARY_DB", "data/library.db"))
    cfg: Config = load_config(config_path)
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    return create_app(deps=deps, config_path=config_path)
