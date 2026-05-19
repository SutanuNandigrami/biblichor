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


def _probe_bookorbit_health(url: str, timeout: float = 2.5) -> bool:
    """Best-effort sync HTTP GET to BookOrbit's /api/v1/health.
    Returns True only on a 200; False on any error/timeout/non-200.
    Phase 6m.iii M-6 startup nudge. Phase 6o.5 (R-I-7) bumped the
    default timeout from 500ms to 2.5s so cold-start BookOrbit
    containers (Postgres migrations can take ~60s) aren't silently
    flagged as unreachable. The probe still blocks the event loop
    briefly — acceptable because it runs exactly once per startup."""
    if not url:
        return False
    import httpx

    try:
        # Phase 6o.7 (R-M-2): support reverse-proxy URLs that include
        # a path prefix (e.g. http://proxy/books). We probe the absolute
        # path, joining onto whatever path the user configured.
        from urllib.parse import urljoin

        probe_url = urljoin(url.rstrip("/") + "/", "api/v1/health")
        r = httpx.get(probe_url, timeout=timeout)
        return r.status_code == 200
    except (httpx.HTTPError, OSError):
        return False


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

    # M-6 startup nudge: warn if BookOrbit is reachable but biblichor's
    # pipeline integration is disabled — means user skipped or aborted
    # `biblichor bookorbit-setup` and books won't appear in the library.
    cfg = app.state.cfg
    if (
        not cfg.bookorbit.enabled
        and cfg.bookorbit.url
        and _probe_bookorbit_health(cfg.bookorbit.url)
    ):
        log.warning(
            "BookOrbit is reachable at %s but cfg.bookorbit.enabled=False — "
            "run `biblichor bookorbit-setup` to wire the pipeline integration",
            cfg.bookorbit.url,
        )

    # Phase 6o.2 (C-1): warn at startup if bookorbit is enabled but
    # library_root doesn't exist inside this process. Catches the host-
    # vs-container path mismatch the live cutover almost shipped.
    if cfg.bookorbit.enabled and cfg.bookorbit.library_root:
        from pathlib import Path as _Path

        if not _Path(cfg.bookorbit.library_root).exists():
            log.warning(
                "bookorbit: library_root=%r does not exist in this process — "
                "books will NOT land in BookOrbit. Re-run `biblichor bookorbit-setup` "
                "from the correct context (e.g. `docker compose exec biblichor ...`).",
                cfg.bookorbit.library_root,
            )
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
