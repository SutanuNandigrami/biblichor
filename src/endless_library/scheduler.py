from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from endless_library.config import Config
from endless_library.db.sources import SourceAccountRow
from endless_library.pipeline import (
    PipelineDeps,
    poll_source_account,
    poll_sources,
    process_queue,
)
from endless_library.scrapers.annas_domains import (
    cache_path as _wiki_cache_path,
)
from endless_library.scrapers.annas_domains import (
    effective_mirrors,
    update_cache_if_stale,
)

log = logging.getLogger(__name__)

JOB_PROCESS = "process"
JOB_MIRRORS = "mirrors_refresh"
JOB_RETENTION = "retention"
JOB_RETRY = "retry"
JOB_SUMMARY = "summary"
SOURCE_JOB_PREFIX = "poll:"  # full id: f"poll:{account_id}"


def source_job_id(account_id: int) -> str:
    return f"{SOURCE_JOB_PREFIX}{account_id}"


def _attach_system_jobs(sched: AsyncIOScheduler, cfg: Config, deps: PipelineDeps) -> None:
    """Attach the three non-source-specific jobs: process, retry, summary."""

    async def _process_job():
        tally = await asyncio.to_thread(process_queue, deps)
        log.info("cycle tally: %s", tally)

    async def _retry_job():
        # retry_failed is just another pass through pending; bookrepo.pending()
        # already covers status='failed' below max_attempts.
        await asyncio.to_thread(process_queue, deps)

    async def _summary_job():
        recent = deps.events.recent_global(limit=2000)
        sent = sum(1 for e in recent if e.kind == "send")
        failed = sum(1 for e in recent if e.kind == "error")
        needs = sum(1 for e in recent if "needs_review" in (e.message or ""))
        deps.notifier.daily_summary(sent=sent, failed=failed, needs_review=needs)

    sched.add_job(
        _process_job,
        "interval",
        minutes=cfg.general.process_interval_minutes,
        id=JOB_PROCESS,
        name="Process the queue (search + download + send)",
        replace_existing=True,
        max_instances=1,
    )
    sched.add_job(
        _retry_job,
        "interval",
        hours=cfg.general.retry_interval_hours,
        id=JOB_RETRY,
        name="Retry failed downloads",
        replace_existing=True,
        max_instances=1,
    )

    async def _mirrors_job():
        cache = _wiki_cache_path(deps.db_path.parent)
        await asyncio.to_thread(update_cache_if_stale, cache)
        cached, _ = await asyncio.to_thread(
            __import__(
                "endless_library.scrapers.annas_domains", fromlist=["read_cache"]
            ).read_cache,
            cache,
        )
        deps.cfg.scrapers.annas_mirrors = effective_mirrors(deps.cfg.scrapers.annas_mirrors, cached)
        log.info("annas mirrors after refresh: %s", deps.cfg.scrapers.annas_mirrors)

    sched.add_job(
        _mirrors_job,
        "interval",
        hours=cfg.general.mirror_refresh_hours,
        id=JOB_MIRRORS,
        name="Refresh Anna's Archive mirrors from Wikipedia",
        replace_existing=True,
        max_instances=1,
    )
    sched.add_job(
        _summary_job,
        "cron",
        hour=cfg.general.daily_summary_hour_utc,
        id=JOB_SUMMARY,
        name="Daily summary (Pushover)",
        replace_existing=True,
        max_instances=1,
    )

    async def _retention_job():
        """Daily DB prune: trim events to ~90 days / 50K rows, cap
        bench history at 200 rows per scraper. Vacuums the DB at the
        end so disk usage actually shrinks.
        """
        deps.events.prune(
            keep_rows=cfg.general.retention_keep_events,
            keep_days=cfg.general.retention_keep_events_days,
        )
        deps.bench.prune(keep_per_scraper=cfg.general.retention_keep_bench_per_scraper)

    sched.add_job(
        _retention_job,
        "cron",
        hour=cfg.general.retention_hour_utc,
        id=JOB_RETENTION,
        name="DB retention (prune events + bench history)",
        replace_existing=True,
        max_instances=1,
    )

    # Seed annas_mirrors from any existing wiki cache file, synchronously,
    # so the first scraper run already sees the merged list. A separate
    # mirrors_refresh job (scheduled above) keeps it current.
    try:
        from endless_library.scrapers.annas_domains import read_cache

        cache = _wiki_cache_path(deps.db_path.parent)
        cached, _ = read_cache(cache)
        if cached:
            deps.cfg.scrapers.annas_mirrors = effective_mirrors(
                deps.cfg.scrapers.annas_mirrors, cached
            )
            log.info("seeded annas mirrors from wiki cache: %s", deps.cfg.scrapers.annas_mirrors)
    except Exception as e:  # pragma: no cover
        log.warning("mirror seed from wiki cache failed: %s", e)


def add_source_job(sched: AsyncIOScheduler, deps: PipelineDeps, account: SourceAccountRow) -> None:
    """Schedule a per-source poll job. Idempotent (replace_existing=True)."""

    aid = account.id
    name = f"Poll {account.source} ({account.identifier})"

    async def _job():
        await asyncio.to_thread(poll_source_account, deps, aid)

    sched.add_job(
        _job,
        "interval",
        minutes=max(1, account.poll_interval_minutes),
        id=source_job_id(aid),
        name=name,
        replace_existing=True,
        max_instances=1,
    )


def remove_source_job(sched: AsyncIOScheduler, account_id: int) -> None:
    """Remove a per-source job. No-op if not present."""
    jid = source_job_id(account_id)
    if sched.get_job(jid) is not None:
        sched.remove_job(jid)


def _attach_source_jobs(sched: AsyncIOScheduler, deps: PipelineDeps) -> None:
    """Register a poll job for every currently-enabled source account."""
    for acct in deps.sources.list_enabled():
        add_source_job(sched, deps, acct)


def _attach_error_listener(sched: AsyncIOScheduler, deps: PipelineDeps) -> None:
    """Surface uncaught job exceptions. Without this, APScheduler logs to
    its own DEBUG logger and the next cycle just runs — operators have
    no idea anything broke.

    The listener:
      - emits a log.exception with full traceback
      - writes a global event row (kind="error", book_id=None) so
        /api/events shows it in the dashboard
      - sends a Pushover ping at high priority if pushover is configured
    """

    def on_job_error(event: JobExecutionEvent) -> None:
        exc = event.exception
        job_id = event.job_id
        log.exception("scheduler job %s crashed", job_id, exc_info=exc)
        try:
            deps.events.append(
                book_id=None,
                kind="error",
                scraper=None,
                message=f"scheduler job {job_id} crashed: {type(exc).__name__}: {exc}",
            )
        except Exception:
            log.exception("failed to record scheduler error event")
        try:
            deps.notifier._send(
                title=f"Scheduler job failed: {job_id}",
                message=f"{type(exc).__name__}: {exc}",
                priority=1,
            )
        except Exception:
            # Notifier failures are non-fatal; we already have log + event
            log.debug("notifier could not deliver scheduler-error alert", exc_info=True)

    sched.add_listener(on_job_error, EVENT_JOB_ERROR)


def build_scheduler(cfg: Config, db_path: Path) -> tuple[AsyncIOScheduler, PipelineDeps]:
    """Build deps + scheduler. Kept for the CLI `endless-library run` entrypoint."""
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_system_jobs(sched, cfg, deps)
    _attach_source_jobs(sched, deps)
    _attach_error_listener(sched, deps)
    return sched, deps


def build_scheduler_with_deps(cfg: Config, deps: PipelineDeps) -> AsyncIOScheduler:
    """Build scheduler around already-constructed deps (FastAPI lifespan path)."""
    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_system_jobs(sched, cfg, deps)
    _attach_source_jobs(sched, deps)
    _attach_error_listener(sched, deps)
    return sched


# Re-export for convenience
__all__ = [
    "JOB_PROCESS",
    "JOB_RETENTION",
    "JOB_RETRY",
    "JOB_SUMMARY",
    "SOURCE_JOB_PREFIX",
    "add_source_job",
    "build_scheduler",
    "build_scheduler_with_deps",
    "poll_sources",  # for /api/run
    "remove_source_job",
    "source_job_id",
]
