from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from endless_library.pipeline import PipelineDeps
from endless_library.web import (
    routes_book,
    routes_health,
    routes_logs,
    routes_queue,
    routes_scrapers,
    routes_settings,
    routes_sources,
)


def create_app(*, deps: PipelineDeps, config_path: Path) -> FastAPI:
    app = FastAPI(title="endless-library", docs_url=None, redoc_url=None)
    templates_dir = Path(__file__).resolve().parent / "web" / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))

    # Stash shared state on the app so routes can access it via request.app.state
    app.state.deps = deps
    app.state.templates = templates
    app.state.config_path = config_path

    routes_queue.register(app)
    routes_book.register(app)
    routes_sources.register(app)
    routes_scrapers.register(app)
    routes_settings.register(app)
    routes_logs.register(app)
    routes_health.register(app)

    @app.get("/", include_in_schema=False)
    def root(request: Request):
        return HTMLResponse('<meta http-equiv="refresh" content="0; url=/queue">')

    return app


def entry() -> FastAPI:
    """uvicorn entry-point: reads CONFIG_PATH and LIBRARY_DB env vars."""
    import os

    from endless_library.config import load_config
    from endless_library.pipeline import PipelineDeps

    config_path = Path(os.environ.get("CONFIG_PATH", "config/config.yaml"))
    db_path = Path(os.environ.get("LIBRARY_DB", "data/library.db"))
    cfg = load_config(config_path)
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    return create_app(deps=deps, config_path=config_path)
