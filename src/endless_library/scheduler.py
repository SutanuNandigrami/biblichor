from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from endless_library.config import Config
from endless_library.pipeline import PipelineDeps, poll_sources, process_queue

log = logging.getLogger(__name__)


def build_scheduler(cfg: Config, db_path: Path) -> tuple[AsyncIOScheduler, PipelineDeps]:
    deps = PipelineDeps.build(cfg=cfg, db_path=db_path)
    sched = AsyncIOScheduler(timezone=cfg.general.timezone)

    async def _poll_job():
        await asyncio.to_thread(poll_sources, deps)

    async def _process_job():
        tally = await asyncio.to_thread(process_queue, deps)
        log.info("cycle tally: %s", tally)

    async def _retry_job():
        # retry_failed is just another pass through pending; the bookrepo.pending()
        # already covers status='failed' below max_attempts
        await asyncio.to_thread(process_queue, deps)

    async def _summary_job():
        # Tally yesterday's events for the summary
        recent = deps.events.recent_global(limit=2000)
        sent = sum(1 for e in recent if e.kind == "send")
        failed = sum(1 for e in recent if e.kind == "error")
        needs = sum(1 for e in recent if "needs_review" in (e.message or ""))
        deps.notifier.daily_summary(sent=sent, failed=failed, needs_review=needs)

    sched.add_job(
        _poll_job,
        "interval",
        minutes=cfg.general.poll_interval_minutes,
        id="poll",
        replace_existing=True,
        max_instances=1,
    )
    sched.add_job(
        _process_job,
        "interval",
        minutes=cfg.general.poll_interval_minutes,
        id="process",
        replace_existing=True,
        max_instances=1,
    )
    sched.add_job(
        _retry_job, "interval", hours=6, id="retry", replace_existing=True, max_instances=1
    )
    sched.add_job(
        _summary_job,
        "cron",
        hour=cfg.general.daily_summary_hour_utc,
        id="summary",
        replace_existing=True,
        max_instances=1,
    )
    return sched, deps
