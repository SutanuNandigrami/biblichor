from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from endless_library.config import Config
from endless_library.pipeline import PipelineDeps, poll_sources, process_queue

log = logging.getLogger(__name__)

# Canonical job IDs; the API knows these names so we keep them stable.
JOB_POLL = "poll"
JOB_PROCESS = "process"
JOB_RETRY = "retry"
JOB_SUMMARY = "summary"


def _attach_jobs(sched: AsyncIOScheduler, cfg: Config, deps: PipelineDeps) -> None:
    """Attach the four core jobs to an already-built scheduler."""

    async def _poll_job():
        await asyncio.to_thread(poll_sources, deps)

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
        _poll_job, "interval",
        minutes=cfg.general.poll_interval_minutes,
        id=JOB_POLL, name="Poll reading-list sources",
        replace_existing=True, max_instances=1,
    )
    sched.add_job(
        _process_job, "interval",
        minutes=cfg.general.poll_interval_minutes,
        id=JOB_PROCESS, name="Process the queue (search + download + send)",
        replace_existing=True, max_instances=1,
    )
    sched.add_job(
        _retry_job, "interval", hours=6,
        id=JOB_RETRY, name="Retry failed downloads",
        replace_existing=True, max_instances=1,
    )
    sched.add_job(
        _summary_job, "cron", hour=cfg.general.daily_summary_hour_utc,
        id=JOB_SUMMARY, name="Daily summary (Pushover)",
        replace_existing=True, max_instances=1,
    )


def build_scheduler(cfg: Config, db_path: Path) -> tuple[AsyncIOScheduler, PipelineDeps]:
    """Build deps + scheduler. Kept for the CLI `endless-library run` entrypoint."""
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_jobs(sched, cfg, deps)
    return sched, deps


def build_scheduler_with_deps(cfg: Config, deps: PipelineDeps) -> AsyncIOScheduler:
    """Build scheduler around already-constructed deps (FastAPI lifespan path)."""
    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_jobs(sched, cfg, deps)
    return sched
