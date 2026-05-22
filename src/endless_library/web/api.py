"""JSON API for the SPA. Replaces the old HTML-rendering routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import asdict
from datetime import UTC
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from endless_library.bench import format_table, load_queries, run_bench
from endless_library.config import save_config
from endless_library.db.schema import connect
from endless_library.url_safety import UnsafeUrlError, assert_safe_url

log = logging.getLogger(__name__)

# ---------- pydantic models for inbound payloads ----------


class AddBook(BaseModel):
    title: str
    author: str | None = None
    isbn13: str | None = None


class _BOSetupPayload(BaseModel):
    admin_username: str
    admin_email: str
    admin_name: str
    admin_password: str
    setup_token: str
    library_root: str | None = None


class _BOCredsPayload(BaseModel):
    admin_username: str
    admin_password: str


class _BOChangePasswordPayload(BaseModel):
    new_password: str
    current_password: str | None = None


class _ZlibCredsPayload(BaseModel):
    email: str
    password: str


class AddSource(BaseModel):
    source: str
    identifier: str
    token: str | None = None
    poll_interval_minutes: int = 60


class RescheduleJob(BaseModel):
    minutes: int | None = None
    hours: int | None = None
    cron_hour: int | None = None  # for daily cron jobs (UTC hour)


class BulkDelete(BaseModel):
    ids: list[int] | None = None
    status: str | None = None
    source: str | None = None
    created_after: str | None = None
    created_before: str | None = None
    hard: bool = False


class SettingsPatch(BaseModel):
    poll_interval_minutes: int | None = None
    max_attempts: int | None = None
    auto_pick_threshold: float | None = None
    auto_pick_gap: float | None = None
    log_level: str | None = None
    kindle_recipient: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    # Phase 6u.6: provider-switch fields. Previously the SPA could only
    # set host/port/user/password; starttls + daily_cap + attachment cap
    # had to be edited by hand in config.yaml. The new provider-preset
    # dropdown depends on these flowing through.
    smtp_starttls: bool | None = None
    smtp_daily_cap: int | None = None
    smtp_max_attachment_mb: int | None = None
    pushover_enabled: bool | None = None
    pushover_user_key: str | None = None
    pushover_app_token: str | None = None
    welib_auth_cookie: str | None = None


def _candidate_mirror(detail_url: str | None) -> str | None:
    """Extract bare host (annas-archive.gl, libgen.li, www.kindlebangla.com)
    from the candidate's detail_url so the SPA can show which specific
    mirror served the result."""
    if not detail_url:
        return None
    from urllib.parse import urlparse

    return urlparse(detail_url).netloc or None


def _compute_bookorbit_urls(request: Request, cfg) -> dict[str, str]:
    """Compute every URL the SPA needs to surface.

    cfg.bookorbit.url is the *internal* API URL biblichor uses to
    talk to BookOrbit (e.g. http://bookorbit:3000 inside docker).
    It is intentionally NOT used here, because a docker service name
    or a 127.0.0.1 address won't resolve from a browser.

    The SPA display URL is, in order of preference:
      1. BOOKORBIT_EXTERNAL_URL env (set this only for reverse-proxy
         setups where BookOrbit lives at a different hostname than
         biblichor)
      2. request.url.hostname + BOOKORBIT_PORT (the published host
         port from compose) — this matches whatever hostname the
         user typed into their browser, so Tailscale names, LAN IPs,
         and localhost all Just Work without per-device config.
    """
    import os

    external = (os.environ.get("BOOKORBIT_EXTERNAL_URL") or "").strip().rstrip("/")
    fallback_port = os.environ.get("BOOKORBIT_PORT", "3000")
    fallback_proto = request.url.scheme or "http"
    fallback_host = request.url.hostname or "localhost"
    fallback_base = f"{fallback_proto}://{fallback_host}:{fallback_port}"

    base = external or fallback_base

    return {
        # Open BookOrbit's dashboard
        "dashboard": base,
        # OPDS catalog — point any e-reader app at this URL (KOReader,
        # Thorium, Moon+ Reader, etc.). Requires user-side OPDS password.
        "opds_catalog": f"{base}/api/v1/opds",
        # Kobo Sync — set as your Kobo device's My Sync URL. Per-device
        # token gets minted when you complete the in-BookOrbit Kobo flow.
        "kobo_sync_root": f"{base}/api/v1/kobo",
        # KOReader two-way sync — uses OPDS (KOReader speaks OPDS, not
        # a dedicated protocol). Same URL as opds_catalog.
        "koreader_sync": f"{base}/api/v1/opds",
        # Reading statistics page
        "statistics": f"{base}/statistics",
        # Web reader prefix — actual reader links are {book_id}/{file_id}
        "reader_base": f"{base}/read",
        # Used by biblichor's own startup-probe / SPA fallback logic
        "base": base,
    }


def register(app: FastAPI) -> None:
    router = APIRouter(prefix="/api")

    # ---------- queue + books ----------

    @router.get("/books")
    def list_books(
        request: Request,
        status: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        """Phase 6u.3: real pagination. `total` is the SQL-filtered match
        count (not just the page slice), so the UI can render
        Page X of Y. limit is clamped to [1,500] and offset to >= 0."""
        deps = request.app.state.deps
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))

        where_clauses: list[str] = []
        params: list = []
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if q:
            where_clauses.append("(LOWER(title) LIKE ? OR LOWER(author) LIKE ?)")
            like = f"%{q.lower()}%"
            params.extend([like, like])
        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        with connect(deps.db_path) as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS n FROM books{where_sql}", params
            ).fetchone()
            total = int(total_row["n"]) if total_row else 0
            rows = conn.execute(
                f"SELECT * FROM books{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {
            "books": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    @router.post("/books")
    def add_book(payload: AddBook, request: Request):
        deps = request.app.state.deps
        bid = deps.books.upsert(
            title=payload.title.strip(),
            author=(payload.author or "").strip() or None,
            isbn13=(payload.isbn13 or "").strip() or None,
            source="manual",
            source_id=f"manual:{payload.title.strip().lower()}",
        )
        deps.events.append(book_id=bid, kind="state_change", message="added manually via dashboard")
        return {"id": bid}

    @router.get("/books/{book_id}")
    def get_book(book_id: int, request: Request):
        """Book detail. Computes score_breakdown for each candidate
        on-the-fly so the drawer's score-explainer UI has the full
        component-level scoring (ISBN/title/author/format/lang/filesize)
        without persisting them in the candidates table."""
        from endless_library.domain.models import Candidate, SearchQuery
        from endless_library.domain.scoring import score_candidate

        deps = request.app.state.deps
        book = deps.books.get(book_id)
        if not book:
            raise HTTPException(404)
        # Reusable SearchQuery for breakdown recomputation
        sq = SearchQuery(
            title=book.title,
            author=book.author,
            isbn13=book.isbn13,
            format_priority=tuple(deps.cfg.scrapers.format_priority),
            language=deps.cfg.scrapers.language,
        )
        candidates = []
        for c in deps.cands.top_for_book(book_id, limit=10):
            d = asdict(c)
            d["mirror"] = _candidate_mirror(c.detail_url)
            # Recompute breakdown from the persisted candidate row
            try:
                raw = json.loads(c.raw_json) if c.raw_json else {}
                cand = Candidate(
                    provider=c.provider,
                    md5=c.md5,
                    title=c.title,
                    author=c.author,
                    language=c.language,
                    format=c.format,
                    filesize_bytes=c.filesize_bytes,
                    year=c.year,
                    publisher=c.publisher,
                    edition_hints=c.edition_hints or "",
                    detail_url=c.detail_url,
                    raw=raw,
                )
                isbn_match = bool(sq.isbn13) and sq.isbn13 in (raw.get("isbns") or [])
                sb = score_candidate(cand, sq, deps.cfg.scoring, isbn13_match=isbn_match)
                d["score_breakdown"] = {
                    "components": sb.components,
                    "is_hard_skip": sb.is_hard_skip,
                    "skip_reason": sb.skip_reason,
                }
            except Exception as e:
                # Don't fail the detail endpoint on score recomputation
                d["score_breakdown"] = {"error": f"{type(e).__name__}: {e}"}
            candidates.append(d)
        events = [
            {**asdict(e), "meta": e.meta} for e in deps.events.recent_for_book(book_id, limit=200)
        ]
        return {
            "book": {**asdict(book)},
            "candidates": candidates,
            "events": events,
        }

    @router.post("/books/{book_id}/retry")
    def retry(book_id: int, request: Request):
        """Full reset → re-queued. Clears stage timestamps + on-disk
        file reference + picked candidate so the next pipeline cycle
        actually re-searches instead of resuming a stale download.
        """
        deps = request.app.state.deps
        if not deps.books.get(book_id):
            raise HTTPException(404)
        deps.books.reset_for_research(book_id)
        deps.cands.clear_for_book(book_id)
        deps.events.append(
            book_id=book_id, kind="state_change", message="manually re-queued from dashboard"
        )
        return {"ok": True}

    @router.post("/books/bulk_delete")
    def bulk_delete(payload: BulkDelete, request: Request):
        """Bulk delete books matching filters. Provide at least one filter.

        Filters combine with AND. `hard=True` removes rows; default soft-deletes
        (status='skipped', preserves audit trail).
        """
        deps = request.app.state.deps
        where: list[str] = []
        args: list = []
        if payload.ids:
            placeholders = ",".join(["?"] * len(payload.ids))
            where.append(f"id IN ({placeholders})")
            args.extend(payload.ids)
        if payload.status:
            where.append("status = ?")
            args.append(payload.status)
        if payload.source:
            where.append("source = ?")
            args.append(payload.source)
        if payload.created_after:
            where.append("created_at >= ?")
            args.append(payload.created_after)
        if payload.created_before:
            where.append("created_at <= ?")
            args.append(payload.created_before)
        if not where:
            raise HTTPException(400, detail="must provide at least one filter")
        clause = " AND ".join(where)
        with connect(deps.db_path) as conn:
            if payload.hard:
                n = conn.execute(f"DELETE FROM books WHERE {clause}", args).rowcount
            else:
                n = conn.execute(
                    f"UPDATE books SET status='skipped', "
                    f"last_error='deleted from dashboard', "
                    f"updated_at=datetime('now') WHERE {clause}",
                    args,
                ).rowcount
            conn.commit()
        return {"deleted": n, "hard": payload.hard}

    @router.post("/books/{book_id}/delete")
    def soft_delete(book_id: int, request: Request):
        deps = request.app.state.deps
        if not deps.books.get(book_id):
            raise HTTPException(404)
        deps.books.set_status(book_id, "skipped", error="deleted from dashboard")
        return {"ok": True}

    @router.post("/books/{book_id}/pick/{cand_id}")
    def pick(book_id: int, cand_id: int, request: Request):
        deps = request.app.state.deps
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

    # ---------- cycle ----------

    @router.post("/cycle/run-now")
    async def run_now(request: Request):
        from endless_library.pipeline import poll_sources, process_queue

        deps = request.app.state.deps
        state = request.app.state
        if getattr(state, "_running", False):
            raise HTTPException(409, detail="already running")
        state._running = True
        state._last_tally = None

        async def _runner():
            try:
                await asyncio.to_thread(poll_sources, deps)
                state._last_tally = await asyncio.to_thread(process_queue, deps)
            except Exception as exc:
                state._last_tally = {"error": str(exc)}
            finally:
                state._running = False

        _t = asyncio.create_task(_runner())
        state._task = _t
        return {"ok": True, "running": True}

    @router.get("/cycle/status")
    def cycle_status(request: Request):
        state = request.app.state
        return {
            "running": bool(getattr(state, "_running", False)),
            "last_tally": getattr(state, "_last_tally", None),
        }

    # ---------- schedule ----------

    def _describe_trigger(trig) -> str:
        t = type(trig).__name__
        if t == "IntervalTrigger":
            sec = int(trig.interval.total_seconds())
            if sec % 3600 == 0:
                return f"every {sec // 3600} hour(s)"
            if sec % 60 == 0:
                return f"every {sec // 60} min"
            return f"every {sec} sec"
        if t == "CronTrigger":
            return f"cron: {trig}"
        return repr(trig)

    @router.get("/schedule/jobs")
    def list_jobs(request: Request):
        sched = getattr(request.app.state, "scheduler", None)
        if sched is None:
            return {"jobs": [], "scheduler_running": False}
        jobs = []
        for j in sched.get_jobs():
            jobs.append(
                {
                    "id": j.id,
                    "name": j.name or j.id,
                    "trigger": _describe_trigger(j.trigger),
                    "next_run_at": j.next_run_time.isoformat() if j.next_run_time else None,
                    "paused": j.next_run_time is None,
                }
            )
        return {"jobs": jobs, "scheduler_running": sched.running}

    @router.post("/schedule/jobs/{job_id}/pause")
    def pause_job(job_id: str, request: Request):
        sched = request.app.state.scheduler
        try:
            sched.pause_job(job_id)
        except Exception as e:
            raise HTTPException(404, detail=str(e)) from e
        return {"ok": True, "paused": True}

    @router.post("/schedule/jobs/{job_id}/resume")
    def resume_job(job_id: str, request: Request):
        sched = request.app.state.scheduler
        try:
            sched.resume_job(job_id)
        except Exception as e:
            raise HTTPException(404, detail=str(e)) from e
        return {"ok": True, "paused": False}

    @router.post("/schedule/jobs/{job_id}/run")
    def run_job(job_id: str, request: Request):
        """Trigger a job to fire on the next scheduler tick."""
        from datetime import datetime

        sched = request.app.state.scheduler
        try:
            sched.modify_job(job_id, next_run_time=datetime.now(sched.timezone))
        except Exception as e:
            raise HTTPException(404, detail=str(e)) from e
        return {"ok": True}

    @router.post("/schedule/jobs/{job_id}/reschedule")
    def reschedule_job(job_id: str, payload: RescheduleJob, request: Request):
        """Reschedule a job and persist the change so it survives restart.

        Mapping of job_id to durable storage:
          - process           -> cfg.general.process_interval_minutes
          - retry             -> cfg.general.retry_interval_hours
          - summary           -> cfg.general.daily_summary_hour_utc
          - poll:<account_id> -> source_accounts.poll_interval_minutes for that row
        """
        sched = request.app.state.scheduler
        deps = request.app.state.deps
        try:
            if payload.cron_hour is not None:
                sched.reschedule_job(job_id, trigger="cron", hour=payload.cron_hour)
            elif payload.hours is not None:
                sched.reschedule_job(job_id, trigger="interval", hours=payload.hours)
            elif payload.minutes is not None:
                sched.reschedule_job(job_id, trigger="interval", minutes=payload.minutes)
            else:
                raise HTTPException(400, detail="provide minutes, hours, or cron_hour")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(404, detail=str(e)) from e

        persisted = False
        if job_id.startswith("poll:"):
            try:
                acct_id = int(job_id.split(":", 1)[1])
            except ValueError:
                acct_id = None
            if acct_id is not None and payload.minutes is not None:
                deps.sources.set_interval(acct_id, payload.minutes)
                persisted = True
        elif job_id == "process" and payload.minutes is not None:
            deps.cfg.general.process_interval_minutes = payload.minutes
            save_config(deps.cfg, request.app.state.config_path)
            persisted = True
        elif job_id == "retry" and payload.hours is not None:
            deps.cfg.general.retry_interval_hours = payload.hours
            save_config(deps.cfg, request.app.state.config_path)
            persisted = True
        elif job_id == "summary" and payload.cron_hour is not None:
            deps.cfg.general.daily_summary_hour_utc = payload.cron_hour
            save_config(deps.cfg, request.app.state.config_path)
            persisted = True
        elif job_id == "mirrors_refresh" and payload.hours is not None:
            deps.cfg.general.mirror_refresh_hours = payload.hours
            save_config(deps.cfg, request.app.state.config_path)
            persisted = True
        elif job_id == "retention" and payload.cron_hour is not None:
            deps.cfg.general.retention_hour_utc = payload.cron_hour
            save_config(deps.cfg, request.app.state.config_path)
            persisted = True
        return {"ok": True, "persisted": persisted}

    # ---------- sources ----------

    @router.get("/sources")
    def list_sources(request: Request):
        deps = request.app.state.deps
        return {"sources": [asdict(s) for s in deps.sources.list_all()]}

    @router.post("/sources")
    def add_source(payload: AddSource, request: Request):
        deps = request.app.state.deps
        # Allowlist must stay in sync with sources/registry.py — any
        # source the registry can build is fair game for the API. Using
        # the registry as the source of truth means future sources
        # auto-enable here.
        from endless_library.sources import registry as _src_reg
        if payload.source not in _src_reg._SOURCES:
            raise HTTPException(400, detail="unknown source")
        try:
            sid = deps.sources.add(
                source=payload.source,
                identifier=payload.identifier,
                token=payload.token,
                poll_interval_minutes=payload.poll_interval_minutes,
            )
        except Exception as e:
            raise HTTPException(400, detail=str(e)) from e
        sched = getattr(request.app.state, "scheduler", None)
        if sched is not None:
            from endless_library.scheduler import add_source_job

            acct = deps.sources.get(sid)
            if acct is not None and acct.enabled:
                add_source_job(sched, deps, acct)
        return {"id": sid}

    @router.post("/sources/{sid}/poll")
    async def poll_one(sid: int, request: Request):
        from endless_library.pipeline import poll_sources

        deps = request.app.state.deps
        added = await asyncio.to_thread(poll_sources, deps)
        return {"ok": True, "added": added}

    @router.post("/sources/{sid}/toggle")
    def toggle_source(sid: int, request: Request):
        deps = request.app.state.deps
        row = deps.sources.get(sid)
        if not row:
            raise HTTPException(404)
        new_enabled = not row.enabled
        deps.sources.set_enabled(sid, new_enabled)
        sched = getattr(request.app.state, "scheduler", None)
        if sched is not None:
            from endless_library.scheduler import add_source_job, remove_source_job

            if new_enabled:
                acct = deps.sources.get(sid)
                if acct is not None:
                    add_source_job(sched, deps, acct)
            else:
                remove_source_job(sched, sid)
        return {"ok": True, "enabled": new_enabled}

    @router.post("/sources/{sid}/delete")
    def delete_source(sid: int, request: Request):
        deps = request.app.state.deps
        sched = getattr(request.app.state, "scheduler", None)
        if sched is not None:
            from endless_library.scheduler import remove_source_job

            remove_source_job(sched, sid)
        deps.sources.delete(sid)
        return {"ok": True}

    # ---------- scrapers ----------

    @router.get("/scrapers")
    def list_scrapers(request: Request):
        deps = request.app.state.deps
        from endless_library.scrapers import registry

        all_names = registry.available()
        stats = {n: deps.bench.success_rate(scraper=n) for n in all_names}
        return {
            "available": all_names,
            "order": deps.cfg.scrapers.order,
            "enabled": deps.cfg.scrapers.enabled,
            "success_rates_30d": stats,
        }

    @router.post("/scrapers/{name}/toggle")
    def toggle_scraper(name: str, request: Request):
        deps = request.app.state.deps
        deps.cfg.scrapers.enabled[name] = not deps.cfg.scrapers.enabled.get(name, False)
        save_config(deps.cfg, request.app.state.config_path)
        return {"ok": True, "enabled": deps.cfg.scrapers.enabled[name]}

    @router.post("/scrapers/order")
    def reorder_scrapers(payload: dict, request: Request):
        deps = request.app.state.deps
        order = payload.get("order") or []
        if not isinstance(order, list):
            raise HTTPException(400, "order must be a list")
        deps.cfg.scrapers.order = [str(x) for x in order]
        save_config(deps.cfg, request.app.state.config_path)
        return {"ok": True, "order": deps.cfg.scrapers.order}

    @router.get("/bench/history")
    def bench_history(request: Request, limit: int = 7):
        """Per-scraper recent bench outcomes. Returns the last N runs for
        each scraper currently in cfg.scrapers.order, oldest-first so the UI
        can paint a left-to-right sparkline.
        """
        deps = request.app.state.deps
        out: dict[str, list[dict]] = {}
        for name in deps.cfg.scrapers.order:
            rows = deps.bench.recent(scraper=name, limit=limit)
            # rows come back newest-first; flip for left-to-right reading
            out[name] = [
                {
                    "ts": r.ts,
                    "query": r.query,
                    "success": r.success,
                    "duration_ms": r.duration_ms,
                    "http_code": r.http_code,
                }
                for r in reversed(rows)
            ]
        # 30-day rolling success rate per scraper
        rates = {
            name: round(deps.bench.success_rate(scraper=name, days=30), 3)
            for name in deps.cfg.scrapers.order
        }
        return {"history": out, "success_rates_30d": rates}

    @router.post("/bench/run")
    async def run_bench_endpoint(request: Request, mode: str = "quick"):
        deps = request.app.state.deps
        qs, quick_idx = load_queries()
        if mode == "quick":
            qs = [qs[i] for i in quick_idx if i < len(qs)]
        from functools import partial

        outcomes = await asyncio.to_thread(partial(run_bench, deps.cfg, qs, repo=deps.bench))
        return {
            "outcomes": [asdict(o) for o in outcomes],
            "table": format_table(outcomes),
        }

    # ---------- settings ----------

    @router.get("/settings")
    def get_settings(request: Request):
        """Return public config + URL surfaces computed against the
        request's host. The SPA reads bookorbit.url etc. from here so
        clicking "Open BookOrbit" always lands at a URL that resolves
        from wherever the SPA itself was loaded (Tailscale name,
        Docker host, reverse proxy, localhost — all work the same).

        Phase 6o.4 (D-1 + U-1..U-4): builds OPDS, Kobo, KOReader, web-
        reader, and statistics URLs alongside the BookOrbit base. The
        SPA Library page shows all of these so users can point their
        e-reader apps at the catalog without copying URLs by hand.
        """
        deps = request.app.state.deps
        pub = deps.cfg.public_view()
        pub["bookorbit"]["urls"] = _compute_bookorbit_urls(request, deps.cfg)
        return pub

    @router.post("/settings")
    def save_settings(patch: SettingsPatch, request: Request):
        deps = request.app.state.deps
        cfg = deps.cfg
        p = patch.model_dump(exclude_unset=True)
        if "poll_interval_minutes" in p:
            cfg.general.poll_interval_minutes = p["poll_interval_minutes"]
        if "max_attempts" in p:
            cfg.general.max_attempts = p["max_attempts"]
        if "auto_pick_threshold" in p:
            cfg.general.auto_pick_threshold = p["auto_pick_threshold"]
        if "auto_pick_gap" in p:
            cfg.general.auto_pick_gap = p["auto_pick_gap"]
        if "log_level" in p:
            cfg.general.log_level = p["log_level"]
        if "kindle_recipient" in p:
            cfg.kindle.recipient = (p["kindle_recipient"] or "").strip()
        if "smtp_host" in p:
            cfg.smtp.host = (p["smtp_host"] or "").strip()
        if "smtp_port" in p:
            cfg.smtp.port = int(p["smtp_port"])
        if "smtp_user" in p:
            cfg.smtp.user = (p["smtp_user"] or "").strip()
        if "smtp_password" in p and p["smtp_password"] and p["smtp_password"] != "***":
            cfg.smtp.password = p["smtp_password"].replace(" ", "").strip()
        # Phase 6u.6: provider-switch fields
        if "smtp_starttls" in p and p["smtp_starttls"] is not None:
            cfg.smtp.starttls = bool(p["smtp_starttls"])
        if "smtp_daily_cap" in p and p["smtp_daily_cap"] is not None:
            cfg.smtp.daily_cap = max(0, int(p["smtp_daily_cap"]))
        if "smtp_max_attachment_mb" in p and p["smtp_max_attachment_mb"] is not None:
            cfg.smtp.max_attachment_mb = max(1, int(p["smtp_max_attachment_mb"]))
        if "pushover_enabled" in p:
            cfg.pushover.enabled = bool(p["pushover_enabled"])
        if "pushover_user_key" in p and p["pushover_user_key"] and p["pushover_user_key"] != "***":
            cfg.pushover.user_key = p["pushover_user_key"].strip()
        if (
            "pushover_app_token" in p
            and p["pushover_app_token"]
            and p["pushover_app_token"] != "***"
        ):
            cfg.pushover.app_token = p["pushover_app_token"].strip()
        if (
            "welib_auth_cookie" in p
            and p["welib_auth_cookie"] is not None
            and p["welib_auth_cookie"] != "***"
        ):
            # "" means user wants to clear it
            value = p["welib_auth_cookie"].strip()
            cfg.scrapers.welib_auth_cookie = value or None
        save_config(cfg, request.app.state.config_path)
        return {"ok": True, "cfg": cfg.public_view()}

    @router.post("/settings/test-smtp")
    def test_smtp(request: Request):
        import asyncio as aio
        from email.message import EmailMessage

        from endless_library.kindle import _send_smtp

        deps = request.app.state.deps
        cfg = deps.cfg
        if not cfg.smtp.host:
            return {"ok": False, "error": "no SMTP host configured"}
        if not cfg.smtp.user or not cfg.smtp.password:
            return {"ok": False, "error": "SMTP user + password required"}
        recipient = cfg.kindle.recipient or cfg.smtp.user
        msg = EmailMessage()
        msg["From"] = cfg.smtp.user
        msg["To"] = recipient
        msg["Subject"] = "endless-library SMTP test"
        msg.set_content(
            "This is a test email from your endless-library install. "
            "If you see it on Kindle, the pipeline can deliver books."
        )
        try:
            r = aio.run(_send_smtp(msg, smtp=cfg.smtp, timeout=20.0))
            return {"ok": True, "recipient": recipient, "response": str(r.response)[:160]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @router.post("/settings/test-pushover")
    def test_pushover(request: Request):
        deps = request.app.state.deps
        cfg = deps.cfg
        if not cfg.pushover.user_key or not cfg.pushover.app_token:
            return {"ok": False, "error": "Pushover keys not configured"}
        prev = cfg.pushover.enabled
        cfg.pushover.enabled = True
        try:
            ok = deps.notifier._send("endless-library test", "Pushover works")
        finally:
            cfg.pushover.enabled = prev
        return {"ok": ok}

    # ---------- logs ----------

    @router.get("/events")
    def list_events(
        request: Request, limit: int = 500, kind: str | None = None, book_id: int | None = None
    ):
        deps = request.app.state.deps
        if book_id:
            rows = deps.events.recent_for_book(book_id, limit=limit)
        else:
            rows = deps.events.recent_global(limit=limit)
        if kind:
            rows = [r for r in rows if r.kind == kind]
        return {"events": [{**asdict(r), "meta": r.meta} for r in rows]}

    # ---------- setup / status ----------

    @router.get("/setup")
    def get_setup_status(request: Request):
        import shutil
        import subprocess

        deps = request.app.state.deps
        cfg = deps.cfg
        sources_n = len(deps.sources.list_enabled())
        calibre_bin = shutil.which("ebook-convert")
        calibre_ver = ""
        if calibre_bin:
            try:
                r = subprocess.run(
                    [calibre_bin, "--version"], capture_output=True, text=True, timeout=5
                )
                calibre_ver = (r.stdout or r.stderr).strip().splitlines()[0][:80]
            except Exception:
                calibre_ver = "(present, version probe failed)"
        return {
            "sources_count": sources_n,
            "smtp_configured": bool(cfg.smtp.host and cfg.smtp.user and cfg.smtp.password),
            "kindle_recipient": cfg.kindle.recipient,
            "smtp_user": cfg.smtp.user,
            "calibre_present": bool(calibre_bin),
            "calibre_version": calibre_ver,
            "last_smtp_probe": getattr(request.app.state, "last_smtp_probe", None),
        }

    @router.post("/setup/probe-smtp")
    def probe_smtp(request: Request):
        import socket

        deps = request.app.state.deps
        host = deps.cfg.smtp.host or "smtp.gmail.com"
        port = int(deps.cfg.smtp.port or 587)
        try:
            with socket.create_connection((host, port), timeout=5):
                result = f"OK {host}:{port}"
        except Exception as e:
            result = f"FAIL {host}:{port} ({e})"
        request.app.state.last_smtp_probe = result
        return {"ok": result.startswith("OK"), "result": result}

    # ---------- healthz (root-level, no /api prefix) ----------

    @app.get("/healthz")
    def healthz(request: Request):
        """Standard health probe. Returns 200 if every component is
        healthy, 503 if anything is down (so docker/k8s/bootstrap.sh
        can wait until the service is actually ready)."""
        from fastapi.responses import JSONResponse

        deps = request.app.state.deps
        components: dict[str, object] = {}
        ok = True

        try:
            with connect(deps.db_path) as conn:
                conn.execute("SELECT 1").fetchone()
            components["db"] = True
        except Exception as e:
            components["db"] = f"down: {type(e).__name__}"
            ok = False

        try:
            from endless_library.scrapers import registry as r

            components["scrapers"] = len(r.available())
        except Exception as e:
            components["scrapers"] = f"down: {type(e).__name__}: {e}"
            ok = False

        sched = getattr(request.app.state, "scheduler", None)
        running = bool(sched and getattr(sched, "running", False))
        components["scheduler"] = running
        if not running:
            ok = False

        try:
            queue_size = len(deps.books.pending(max_attempts=10_000))
            components["queue_size"] = queue_size
        except Exception:
            components["queue_size"] = None

        try:
            components["db_size_bytes"] = Path(deps.db_path).stat().st_size
        except Exception:
            components["db_size_bytes"] = 0

        # Phase 6u: SMTP quota visibility. Counts kind='send' events in
        # the last 24h vs cfg.smtp.daily_cap so the dashboard can warn
        # before Gmail throttles the pipeline.
        try:
            from endless_library.smtp_rate import quota_status as _smtp_quota

            q = _smtp_quota(deps.db_path, daily_cap=deps.cfg.smtp.daily_cap)
            components["smtp"] = {
                "sent_24h": q.sent_24h,
                "cap": q.cap,
                "remaining": q.remaining,
                "exhausted": q.exhausted,
            }
        except Exception as e:  # pragma: no cover
            components["smtp"] = f"unknown: {type(e).__name__}: {e}"

        body = {"ok": ok, **components}
        if not ok:
            return JSONResponse(status_code=503, content=body)
        return body

    # ---------- mirrors ----------

    @router.get("/mirrors/effective")
    def mirrors_effective(request: Request):
        """Show the *effective* Anna's mirror list scrapers actually use.

        Sources:
          - configured: cfg.scrapers.annas_mirrors (user-edited config.yaml)
          - wiki:       cached domains from data/wiki_annas_domains.json
          - merged:     what scrapers see right now (after wiki refresh has
                        merged the two — see scrapers/annas_domains.py)

        Also returns last/next wiki refresh timestamps so the UI can show
        when the cache was last updated and when the next 6-hourly job runs.
        """
        from datetime import datetime, timedelta

        from endless_library.scrapers.annas_domains import (
            cache_path,
            effective_mirrors,
            read_cache,
        )

        deps = request.app.state.deps
        configured = list(deps.cfg.scrapers.annas_mirrors or [])
        wiki, ts = read_cache(cache_path(deps.db_path.parent))
        merged = effective_mirrors(configured, wiki)
        last_iso: str | None = None
        next_iso: str | None = None
        if ts > 0:
            last_dt = datetime.fromtimestamp(ts, tz=UTC)
            last_iso = last_dt.isoformat()
            interval = deps.cfg.general.mirror_refresh_hours
            next_iso = (last_dt + timedelta(hours=interval)).isoformat()
        return {
            "configured": configured,
            "wiki": wiki,
            "merged": merged,
            "last_refresh_at": last_iso,
            "next_refresh_at": next_iso,
            "refresh_interval_hours": deps.cfg.general.mirror_refresh_hours,
        }

    @router.get("/mirrors")
    def list_mirrors(request: Request):
        from dataclasses import asdict

        deps = request.app.state.deps
        rows = deps.mirrors.list_all()
        return {"mirrors": [asdict(r) for r in rows]}

    @router.post("/mirrors")
    def add_mirror(payload: dict, request: Request):
        deps = request.app.state.deps
        kind = payload.get("kind")
        url = (payload.get("url") or "").strip()
        if not kind or not url:
            raise HTTPException(400, "kind + url required")
        # SSRF guard: reject loopback / link-local / RFC1918 / non-http schemes
        # before storing OR probing. assert_safe_url resolves DNS, so a
        # public hostname that resolves to 127.0.0.1 is also caught.
        try:
            assert_safe_url(url)
        except UnsafeUrlError as e:
            raise HTTPException(400, f"unsafe mirror URL: {e}") from e
        try:
            mid = deps.mirrors.add(kind=kind, url=url, label=payload.get("label"))
        except Exception as e:
            raise HTTPException(400, str(e)) from e
        return {"id": mid}

    @router.post("/mirrors/{mid}/probe")
    async def probe_mirror(mid: int, request: Request):
        import asyncio

        from endless_library.probes import probe_http

        deps = request.app.state.deps
        row = deps.mirrors.get(mid)
        if not row:
            raise HTTPException(404)
        result = await asyncio.to_thread(probe_http, row.url)
        deps.mirrors.record_probe(
            mid,
            ok=result.ok,
            status=result.status,
            latency_ms=result.latency_ms,
            error=result.error,
        )
        return {
            "ok": result.ok,
            "status": result.status,
            "latency_ms": result.latency_ms,
            "error": result.error,
        }

    @router.post("/mirrors/probe-all")
    async def probe_all_mirrors(request: Request):
        import asyncio

        from endless_library.probes import probe_http

        deps = request.app.state.deps
        rows = deps.mirrors.list_all()

        async def _one(row):
            r = await asyncio.to_thread(probe_http, row.url)
            deps.mirrors.record_probe(
                row.id,
                ok=r.ok,
                status=r.status,
                latency_ms=r.latency_ms,
                error=r.error,
            )
            return {
                "id": row.id,
                "url": row.url,
                "ok": r.ok,
                "status": r.status,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }

        results = await asyncio.gather(*(_one(r) for r in rows))
        return {"results": list(results)}

    @router.post("/mirrors/{mid}/toggle")
    def toggle_mirror(mid: int, request: Request):
        deps = request.app.state.deps
        row = deps.mirrors.get(mid)
        if not row:
            raise HTTPException(404)
        deps.mirrors.set_enabled(mid, not row.enabled)
        return {"ok": True, "enabled": not row.enabled}

    @router.post("/mirrors/{mid}/delete")
    def delete_mirror(mid: int, request: Request):
        deps = request.app.state.deps
        deps.mirrors.delete(mid)
        return {"ok": True}

    # ---------- scoring ----------

    @router.get("/scoring")
    def get_scoring(request: Request):
        deps = request.app.state.deps
        return deps.cfg.scoring.model_dump()

    @router.post("/scoring")
    def set_scoring(payload: dict, request: Request):
        deps = request.app.state.deps
        cfg = deps.cfg
        for field, value in payload.items():
            if hasattr(cfg.scoring, field):
                setattr(cfg.scoring, field, value)
        from endless_library.config import save_config

        save_config(cfg, request.app.state.config_path)
        return {"ok": True, "scoring": cfg.scoring.model_dump()}

    @router.post("/scoring/reset")
    def reset_scoring(request: Request):
        from endless_library.config import ScoringCfg, save_config

        deps = request.app.state.deps
        deps.cfg.scoring = ScoringCfg(
            isbn_match=35,
            title_weight=25,
            author_weight=15,
            format_bonus={"epub": 10, "azw3": 9, "mobi": 8, "pdf": 5},
            language_bonus=10,
            filesize_min_bytes=200_000,
            filesize_max_bytes=80 * 1024 * 1024,
            scan_penalty=10,
            audio_keywords=["audiobook", "audible", "mp3", "m4b"],
        )
        save_config(deps.cfg, request.app.state.config_path)
        return {"ok": True, "scoring": deps.cfg.scoring.model_dump()}

    @router.post("/scoring/preview")
    def score_preview(payload: dict, request: Request):
        """Re-score one book's candidates against either the proposed weights
        (passed in `weights`) or the current config. Returns rankings."""
        from endless_library.config import ScoringCfg
        from endless_library.domain.models import Candidate, SearchQuery
        from endless_library.domain.scoring import score_candidate

        deps = request.app.state.deps
        book_id = int(payload.get("book_id") or 0)
        book = deps.books.get(book_id) if book_id else None
        if not book:
            raise HTTPException(404, "book not found")
        # Build scoring cfg from payload override (partial allowed)
        base = deps.cfg.scoring.model_dump()
        for k, v in (payload.get("weights") or {}).items():
            if k in base:
                base[k] = v
        scoring_cfg = ScoringCfg(**base)
        q = SearchQuery(
            title=book.title,
            author=book.author,
            isbn13=book.isbn13,
            format_priority=tuple(deps.cfg.scrapers.format_priority),
            language=deps.cfg.scrapers.language,
        )
        # Reconstruct candidates from DB
        import json

        out = []
        for row in deps.cands.top_for_book(book_id, limit=10):
            raw = json.loads(row.raw_json) if row.raw_json else {}
            cand = Candidate(
                provider=row.provider,
                md5=row.md5,
                title=row.title,
                author=row.author,
                language=row.language,
                format=row.format,
                filesize_bytes=row.filesize_bytes,
                year=row.year,
                publisher=row.publisher,
                edition_hints=row.edition_hints or "",
                detail_url=row.detail_url,
                raw=raw,
            )
            isbn_match = bool(q.isbn13) and q.isbn13 in (raw.get("isbns") or [])
            sb = score_candidate(cand, q, scoring_cfg, isbn13_match=isbn_match)
            out.append(
                {
                    "id": row.id,
                    "md5": row.md5,
                    "title": row.title,
                    "format": row.format,
                    "filesize_bytes": row.filesize_bytes,
                    "language": row.language,
                    "isbn_match": isbn_match,
                    "score": sb.total,
                    "components": sb.components,
                    "is_hard_skip": sb.is_hard_skip,
                    "skip_reason": sb.skip_reason,
                }
            )
        out.sort(key=lambda x: x["score"], reverse=True)
        return {
            "book": {
                "id": book.id,
                "title": book.title,
                "isbn13": book.isbn13,
                "author": book.author,
            },
            "candidates": out,
            "weights": scoring_cfg.model_dump(),
        }

    @router.post("/books/{book_id}/tags")
    def set_book_tags(book_id: int, payload: dict, request: Request):
        deps = request.app.state.deps
        if not deps.books.get(book_id):
            raise HTTPException(404)
        series = payload.get("series")
        tags = payload.get("tags")
        if isinstance(tags, list):
            tags = ",".join(str(t).strip() for t in tags if str(t).strip())
        deps.books.set_tags(book_id, series=series, tags=tags)
        return {"ok": True}

    # ---------- BookOrbit admin (Phase 6p.2) ----------

    def _bookorbit_service(request: Request):
        """Instantiate a BookOrbitService bound to this app's cfg/db.
        Each handler gets its own instance — they're cheap."""
        from endless_library.bookorbit.service import BookOrbitService

        deps = request.app.state.deps
        cfg = deps.cfg
        secrets_dir = Path(cfg.general.books_dir).parent / "secrets"
        return BookOrbitService(
            cfg=cfg,
            db_path=deps.db_path,
            restore_key_path=secrets_dir / "restore.key",
        )

    @router.get("/bookorbit/status")
    def bookorbit_status(request: Request):
        """Drives the SPA's Library page: which card to show, what
        the doctor would say without round-tripping the full probe."""
        svc = _bookorbit_service(request)
        from dataclasses import asdict

        return asdict(svc.status())

    @router.post("/bookorbit/setup")
    def bookorbit_setup(payload: _BOSetupPayload, request: Request):
        """First-run admin creation + library creation + creds storage."""
        from endless_library.bookorbit.service import BookOrbitServiceError

        config_path = request.app.state.config_path
        svc = _bookorbit_service(request)
        try:
            result = svc.run_setup(
                admin_username=payload.admin_username,
                admin_email=payload.admin_email,
                admin_name=payload.admin_name,
                admin_password=payload.admin_password,
                setup_token=payload.setup_token,
                library_root=payload.library_root,
                biblichor_config_yaml_path=config_path,
            )
        except BookOrbitServiceError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(500, f"{type(e).__name__}: {e}") from e
        return {"ok": True, "library_id": result.library_id}

    @router.post("/bookorbit/creds")
    def bookorbit_store_creds(payload: _BOCredsPayload, request: Request):
        """Update stored admin credentials without re-running setup.
        Use this when you've changed the admin password directly in
        BookOrbit's own UI and want biblichor to know the new one."""
        svc = _bookorbit_service(request)
        svc.store_admin_creds(payload.admin_username, payload.admin_password)
        return {"ok": True}

    @router.post("/bookorbit/admin/change-password")
    def bookorbit_change_password(payload: _BOChangePasswordPayload, request: Request):
        """Rotate the BookOrbit admin password. Calls BookOrbit's
        POST /auth/change-password and stores the new password in
        the encrypted secrets store."""
        from endless_library.bookorbit.service import BookOrbitServiceError

        svc = _bookorbit_service(request)
        try:
            return svc.change_admin_password(
                new_password=payload.new_password,
                current_password=payload.current_password,
            )
        except BookOrbitServiceError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"{type(e).__name__}: {e}") from e

    @router.post("/scrapers/zlibrary/creds")
    def zlibrary_store_creds(payload: _ZlibCredsPayload, request: Request):
        """Phase 6s.5: store Z-Library SingleLogin creds in the encrypted
        secrets store. SPA Scrapers page card uses this."""
        svc = _bookorbit_service(request)
        svc.store_zlib_creds(payload.email, payload.password)
        return {"ok": True}

    @router.delete("/scrapers/zlibrary/creds")
    def zlibrary_clear_creds(request: Request):
        svc = _bookorbit_service(request)
        svc.clear_zlib_creds()
        return {"ok": True}

    @router.post("/scrapers/cookies")
    async def upload_cookies(request: Request):
        """Phase 6s.5: accept a Netscape-format cookies.txt upload.
        Parses with http.cookiejar.MozillaCookieJar and stores per-
        domain in the encrypted secrets store."""
        import http.cookiejar
        import json as _json
        import tempfile
        from pathlib import Path as _Path

        form = await request.form()
        upfile = form.get("file")
        if upfile is None or not hasattr(upfile, "read"):
            raise HTTPException(400, "missing form field 'file'")
        raw = await upfile.read()
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            fh.write(raw)
            fh.flush()
            path = _Path(fh.name)
        try:
            jar = http.cookiejar.MozillaCookieJar(str(path))
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception as e:
            raise HTTPException(400, f"could not parse Netscape cookies.txt: {e}") from e
        finally:
            path.unlink(missing_ok=True)

        by_domain: dict[str, list[tuple[str, str]]] = {}
        for c in jar:
            by_domain.setdefault(c.domain.lstrip("."), []).append((c.name, c.value))

        svc = _bookorbit_service(request)
        for domain, pairs in by_domain.items():
            svc.set_secret_value(f"cookies.{domain}", _json.dumps(pairs))
        return {"ok": True, "domains": list(by_domain.keys())}

    @router.delete("/bookorbit/creds")
    def bookorbit_clear_creds(request: Request):
        svc = _bookorbit_service(request)
        svc.clear_admin_creds()
        return {"ok": True}

    @router.post("/bookorbit/doctor")
    def bookorbit_doctor(request: Request):
        svc = _bookorbit_service(request)
        report = svc.doctor()
        return {
            "ok": report.ok,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in report.checks],
        }

    @router.post("/bookorbit/recreate-library")
    def bookorbit_recreate_library(request: Request):
        """Use stored credentials to login + ensure the biblichor
        library exists. Recovery path for: library deleted in
        BookOrbit's own UI, or library_id drift between config.yaml
        and BookOrbit's actual state."""
        from endless_library.bookorbit.service import BookOrbitServiceError

        svc = _bookorbit_service(request)
        try:
            return svc.recreate_watched_library()
        except BookOrbitServiceError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"{type(e).__name__}: {e}") from e

    @router.post("/bookorbit/scan")
    def bookorbit_scan(request: Request):
        from endless_library.bookorbit.service import BookOrbitServiceError

        svc = _bookorbit_service(request)
        try:
            return svc.trigger_scan()
        except BookOrbitServiceError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"{type(e).__name__}: {e}") from e

    @router.post("/bookorbit/setup-token")
    def bookorbit_generate_setup_token():
        """Return a fresh 48-byte URL-safe setup token. Used by the
        SPA wizard when the operator doesn't have BOOKORBIT_SETUP_TOKEN
        in their .env yet."""
        from endless_library.bookorbit.service import BookOrbitService

        return {"token": BookOrbitService.generate_setup_token()}

    app.include_router(router)

    # ---------- WebSocket: live events ----------

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        deps = socket.app.state.deps
        # Send the most recent N events as a backlog so the client paints immediately
        try:
            backlog = deps.events.recent_global(limit=50)
            for ev in reversed(backlog):
                await socket.send_text(
                    json.dumps({"type": "event", "data": {**asdict(ev), "meta": ev.meta}})
                )
        except Exception as e:
            log.warning("ws backlog send failed: %s", e)

        # Tail events: poll the events table every 1s for new ids
        last_id = backlog[0].id if backlog else 0
        try:
            while True:
                await asyncio.sleep(1)
                with connect(deps.db_path) as conn:
                    rows = conn.execute(
                        "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT 100",
                        (last_id,),
                    ).fetchall()
                for r in rows:
                    payload: dict[str, Any] = {
                        "id": r["id"],
                        "ts": r["ts"],
                        "kind": r["kind"],
                        "scraper": r["scraper"],
                        "message": r["message"],
                        "book_id": r["book_id"],
                        "meta": json.loads(r["meta_json"]) if r["meta_json"] else {},
                    }
                    await socket.send_text(json.dumps({"type": "event", "data": payload}))
                    last_id = r["id"]
                # Also push cycle status pulse every tick
                state = socket.app.state
                await socket.send_text(
                    json.dumps(
                        {
                            "type": "cycle",
                            "data": {
                                "running": bool(getattr(state, "_running", False)),
                                "last_tally": getattr(state, "_last_tally", None),
                            },
                        }
                    )
                )
        except WebSocketDisconnect:
            return
        except Exception as e:
            log.warning("ws loop error: %s", e)
            with contextlib.suppress(Exception):
                await socket.close()
