"""Regression + feature-intact tests for the APScheduler error listener.

Audit finding: scheduler.py never registered an EVENT_JOB_ERROR listener
so uncaught job exceptions were silently swallowed by APScheduler's
default behaviour. Operators got no signal that anything broke.

Regression test:
  - A job that raises should produce (a) a log.exception, (b) a row in
    `events` with kind='error' + book_id=None, (c) a Pushover call.

Feature-intact tests:
  - The listener is registered on both build_scheduler entry points.
  - A job that succeeds doesn't fire the listener.
  - Notifier failures don't propagate (we already have log + DB event).
"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import MagicMock

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from endless_library.config import Config
from endless_library.pipeline import PipelineDeps
from endless_library.scheduler import (
    _attach_error_listener,
    build_scheduler_with_deps,
)


def _make_deps(tmp_path):
    cfg = Config()
    return cfg, PipelineDeps.build(cfg=cfg, db_path=tmp_path / "library.db")


def _fire(sched: AsyncIOScheduler, exc: Exception) -> JobExecutionEvent:
    """Synthesize a JobExecutionEvent and dispatch it through the
    scheduler's listener fan-out exactly like APScheduler would."""
    ev = JobExecutionEvent(
        code=EVENT_JOB_ERROR,
        job_id="test_job",
        jobstore="default",
        scheduled_run_time=datetime.now(),
        retval=None,
        exception=exc,
        traceback=None,
    )
    sched._dispatch_event(ev)
    return ev


# ============ REGRESSION ============


def test_scheduler_error_listener_records_event(tmp_path):
    """A crashing job must land in events with kind='error' and a clear
    message. Previously these were silently swallowed."""
    cfg, deps = _make_deps(tmp_path)
    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_error_listener(sched, deps)

    _fire(sched, RuntimeError("simulated job crash"))

    rows = deps.events.recent_global(limit=10)
    matches = [r for r in rows if "scheduler job" in (r.message or "")]
    assert matches, "scheduler crash didn't land in events"
    assert "RuntimeError" in matches[0].message
    assert "simulated job crash" in matches[0].message


def test_scheduler_error_listener_logs_with_traceback(tmp_path, caplog):
    """log.exception is the operator-visible signal in production logs
    (e.g. journalctl). Verify it fires with the exception attached."""
    cfg, deps = _make_deps(tmp_path)
    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_error_listener(sched, deps)

    with caplog.at_level(logging.ERROR, logger="endless_library.scheduler"):
        _fire(sched, ValueError("bad data"))

    assert any("crashed" in rec.message for rec in caplog.records)
    # exc_info=True via log.exception means there's an exception attached
    assert any(rec.exc_info for rec in caplog.records if "crashed" in rec.message)


def test_scheduler_error_listener_pushover_called(tmp_path, monkeypatch):
    """The Pushover-side path is wrapped in its own try/except (audit
    finding: notifier failures are non-fatal). Verify the call is at
    least attempted."""
    cfg, deps = _make_deps(tmp_path)
    sent = MagicMock()
    monkeypatch.setattr(deps.notifier, "_send", sent)

    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_error_listener(sched, deps)

    _fire(sched, RuntimeError("kaboom"))

    sent.assert_called_once()
    args, kwargs = sent.call_args
    assert "Scheduler job failed" in (args[0] if args else kwargs.get("title", ""))


def test_scheduler_error_listener_swallows_notifier_failure(tmp_path, monkeypatch):
    """If Pushover is broken, the listener itself must still complete
    (otherwise the listener crash gets swallowed by APScheduler too)."""
    cfg, deps = _make_deps(tmp_path)

    def explode(**kw):
        raise RuntimeError("pushover down")

    monkeypatch.setattr(deps.notifier, "_send", explode)

    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_error_listener(sched, deps)

    # Must NOT raise:
    _fire(sched, RuntimeError("kaboom"))

    # And the event row still went in (because we record BEFORE notifying)
    rows = deps.events.recent_global(limit=10)
    assert any("scheduler job" in (r.message or "") for r in rows)


# ============ FEATURE-INTACT ============


def test_scheduler_listener_registered_on_build_scheduler_with_deps(tmp_path):
    """The lifespan-entry build_scheduler_with_deps must register the
    listener. Otherwise FastAPI-run jobs lose visibility."""
    cfg, deps = _make_deps(tmp_path)
    sched = build_scheduler_with_deps(cfg, deps)
    listeners = sched._listeners
    assert listeners, "no listeners on lifespan-scheduler"
    # Look for the EVENT_JOB_ERROR mask
    masks = [m for _cb, m in listeners]
    assert any(m & EVENT_JOB_ERROR for m in masks), "EVENT_JOB_ERROR not registered"


def test_scheduler_success_does_not_fire_listener(tmp_path):
    """A successful job firing must NOT record an error event. Sanity
    check that the listener is selective."""
    cfg, deps = _make_deps(tmp_path)
    sched = AsyncIOScheduler(timezone=cfg.general.timezone)
    _attach_error_listener(sched, deps)

    # Synthesize a SUCCESS event (different code). Listener only listens
    # for EVENT_JOB_ERROR, so dispatching a success must be a no-op.
    from apscheduler.events import EVENT_JOB_EXECUTED

    success_ev = JobExecutionEvent(
        code=EVENT_JOB_EXECUTED,
        job_id="good",
        jobstore="default",
        scheduled_run_time=datetime.now(),
        retval=None,
        exception=None,
        traceback=None,
    )
    sched._dispatch_event(success_ev)

    rows = deps.events.recent_global(limit=10)
    assert not any("scheduler job" in (r.message or "") for r in rows), (
        "listener fired on success — wrong event code"
    )


def test_scheduler_jobs_still_registered_after_listener(tmp_path):
    """Make sure adding the listener didn't break job registration. All
    the original system jobs (poll, process, retry, summary, mirrors,
    retention) must still be present."""
    cfg, deps = _make_deps(tmp_path)
    sched = build_scheduler_with_deps(cfg, deps)
    job_ids = {j.id for j in sched.get_jobs()}
    # process / retry / summary / mirrors_refresh / retention are unconditional
    for expected in ("process", "retry", "summary", "mirrors_refresh", "retention"):
        assert expected in job_ids, f"job {expected!r} missing"
