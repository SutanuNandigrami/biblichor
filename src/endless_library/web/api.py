"""JSON API for the SPA. Replaces the old HTML-rendering routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from endless_library.bench import load_queries, run_bench
from endless_library.config import save_config
from endless_library.db.schema import connect
from endless_library.kindle_stk import (
    KindleStkAuthExpired,
    KindleStkNotConfigured,
    KindleStkRateLimited,
    KindleStkService,
    KindleStkUploadFailed,
)
from endless_library.url_safety import UnsafeUrlError, assert_safe_url

log = logging.getLogger(__name__)

# SSE bench stream poll interval — module-level so tests can monkeypatch.
# monkeypatch.setattr("endless_library.web.api.SSE_POLL_INTERVAL_SEC", 0.05)
SSE_POLL_INTERVAL_SEC: float = 2.0

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


class _MobilismCredsPayload(BaseModel):
    username: str
    password: str


class _AddFromSearchPayload(BaseModel):
    md5: str
    title: str
    author: str | None = None
    isbn13: str | None = None
    language: str | None = None
    format: str | None = None
    filesize_bytes: int | None = None
    year: int | None = None
    publisher: str | None = None
    detail_url: str
    provider: str = "annas"


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


# Phase 6w ultrareview C3: consolidated scraper→site mapping lives in registry.
from endless_library.scrapers.registry import (  # noqa: E402
    SCRAPER_TO_OPEN_SLUM_SITE as _SCRAPER_TO_SITE,
)


def _scraper_upstream_status(request) -> dict[str, dict]:
    """Return per-scraper upstream status from OpenSlumMonitor, if available.

    Only emits entries for scrapers that have a known site mapping AND for
    which the monitor has data.  Scrapers without a mapping are omitted.
    """
    slum = getattr(request.app.state, "open_slum_monitor", None)
    if slum is None:
        return {}
    result: dict[str, dict] = {}
    seen_sites: dict[str, dict | None] = {}
    for scraper_name, site_name in _SCRAPER_TO_SITE.items():
        if site_name not in seen_sites:
            seen_sites[site_name] = slum.get(site_name)
        status = seen_sites[site_name]
        if status is not None:
            result[scraper_name] = status
    return result


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
        """Full re-search reset. Clears picked candidate + every
        stage timestamp so the next pipeline cycle re-searches
        from scratch. Use when the original pick was wrong.
        For 'download failed, try the same file again', see
        /retry-download which preserves the pick.
        """
        deps = request.app.state.deps
        if not deps.books.get(book_id):
            raise HTTPException(404)
        deps.books.reset_for_research(book_id)
        deps.cands.clear_for_book(book_id)
        deps.events.append(
            book_id=book_id,
            kind="state_change",
            message="manually re-queued from dashboard (full re-search)",
        )
        return {"ok": True, "mode": "research"}

    @router.post("/books/{book_id}/retry-download")
    def retry_download(book_id: int, request: Request):
        """Retry the download with the existing picked candidate.
        Use when a previous download failed for a transient reason
        (CDN drop, IPFS 5xx) and the picked candidate is still the
        right book. Falls back to full re-search if there is no
        picked candidate yet (so the button is never a no-op).
        """
        deps = request.app.state.deps
        book = deps.books.get(book_id)
        if not book:
            raise HTTPException(404)
        if book.picked_candidate_id is None:
            deps.books.reset_for_research(book_id)
            deps.cands.clear_for_book(book_id)
            deps.events.append(
                book_id=book_id,
                kind="state_change",
                message="manually re-queued from dashboard (no prior pick → re-search)",
            )
            return {"ok": True, "mode": "research"}
        pcid = book.picked_candidate_id
        deps.books.reset_for_redownload(book_id)
        deps.events.append(
            book_id=book_id,
            kind="state_change",
            message=f"manually re-queued from dashboard (retry download, pick #{pcid} preserved)",
        )
        return {"ok": True, "mode": "redownload", "picked_candidate_id": pcid}

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
            try:
                dt_after = datetime.fromisoformat(payload.created_after)
            except ValueError as e:
                raise HTTPException(400, detail=f"invalid created_after: {e}") from e
            where.append("created_at >= ?")
            # C-NEW-2: use space-separated format to match SQLite stored format
            # (.isoformat() emits T-separator; SQLite stores YYYY-MM-DD HH:MM:SS)
            if dt_after.tzinfo is not None:
                dt_after = dt_after.astimezone(UTC).replace(tzinfo=None)
            args.append(dt_after.strftime("%Y-%m-%d %H:%M:%S"))
        if payload.created_before:
            try:
                dt_before = datetime.fromisoformat(payload.created_before)
            except ValueError as e:
                raise HTTPException(400, detail=f"invalid created_before: {e}") from e
            where.append("created_at <= ?")
            if dt_before.tzinfo is not None:
                dt_before = dt_before.astimezone(UTC).replace(tzinfo=None)
            args.append(dt_before.strftime("%Y-%m-%d %H:%M:%S"))
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
        from endless_library.bench import load_corpus_tags
        from endless_library.scrapers import registry

        all_names = registry.available()
        in_chain_set = set(registry.enabled_order(deps.cfg.scrapers))
        try:
            corpus_tags = load_corpus_tags()
        except Exception:
            corpus_tags = {}
        ever_run = {n: deps.bench.ever_run(scraper=n) for n in all_names}
        last_run = {n: deps.bench.last_run_at(scraper=n) for n in all_names}
        stats = {n: deps.bench.success_rate(scraper=n) for n in all_names}
        return {
            "available": all_names,
            "order": deps.cfg.scrapers.order,
            "enabled": deps.cfg.scrapers.enabled,
            "success_rates_30d": stats,
            # Phase 6v.4: differentiate "0% — never tested" from "0% — broken"
            "ever_run": ever_run,
            "last_run_at": last_run,
            # in_chain = enabled AND present in cfg.order. A scraper toggled
            # 'enabled' that isn't in the order list won't actually be tried
            # by the pipeline — the SPA shows this distinction so users
            # don't wonder why their flipped switch did nothing.
            "in_chain": {n: (n in in_chain_set) for n in all_names},
            # Specialised corpus this scraper is benched against; empty
            # tuple = general-purpose (sees every query).
            "corpus_tags": {n: sorted(corpus_tags.get(n, frozenset())) for n in all_names},
            # Phase 6w.9e: upstream status from OpenSlumMonitor (optional)
            "upstream_status": _scraper_upstream_status(request),
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

    @router.get("/scrapers/{name}/excluded-categories")
    def get_excluded_categories(name: str, request: Request):
        deps = request.app.state.deps
        per_source = getattr(deps.cfg.scrapers, name, None)
        if per_source is None:
            raise HTTPException(404, f"no per-source config for: {name}")
        cats = getattr(per_source, "excluded_categories", None) or []
        return {"excluded_categories": cats}

    @router.put("/scrapers/{name}/excluded-categories")
    def put_excluded_categories(name: str, payload: dict, request: Request):
        deps = request.app.state.deps
        per_source = getattr(deps.cfg.scrapers, name, None)
        if per_source is None:
            raise HTTPException(404, f"no per-source config for: {name}")
        cats = payload.get("excluded_categories")
        if not isinstance(cats, list):
            raise HTTPException(400, "excluded_categories must be a list")
        per_source.excluded_categories = [str(c) for c in cats]
        save_config(deps.cfg, request.app.state.config_path)
        return {"ok": True, "excluded_categories": per_source.excluded_categories}

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

    @router.post("/bench/run", status_code=202)
    async def run_bench_endpoint(request: Request, mode: str = "quick"):
        deps = request.app.state.deps
        qs, quick_idx = load_queries()
        if mode == "quick":
            qs = [qs[i] for i in quick_idx if i < len(qs)]
        from endless_library.bench import load_corpus_tags, queries_for_scraper
        from endless_library.scrapers import registry

        strats = registry.enabled_order(deps.cfg.scrapers)
        # I5: load corpus_tags once -- not once per scraper inside the worker
        # (each call to run_bench(corpus_tags=None) would reload from disk).
        try:
            corpus_tags = load_corpus_tags()
        except Exception as _e:
            log.warning("bench: could not load corpus_tags: %s", _e)
            corpus_tags = {}
        # I6: progress_total = actual query count per scraper (not len*len).
        # Each scraper runs only against its corpus-filtered subset; using
        # len(strats)*len(qs) overcounts and the progress bar never reaches 100%.
        progress_total = sum(len(queries_for_scraper(qs, s, corpus_tags)) for s in strats)
        job_id = deps.bench_jobs.create(mode=mode, progress_total=progress_total)
        asyncio.create_task(_bench_worker(deps, job_id, qs, strats, corpus_tags))
        return {"job_id": job_id}

    async def _bench_worker(deps, job_id: int, qs, strats, corpus_tags: dict):
        """Background worker: runs bench per-scraper, increments progress, checks cancel.

        corpus_tags is pre-loaded once by the caller (ultrareview I5) so we avoid
        one YAML file read per scraper -- run_bench(corpus_tags=None) re-reads the
        file on every call.
        """
        from functools import partial

        try:
            outcomes_so_far: list = []
            for s_name in strats:
                if deps.bench_jobs.is_cancel_requested(job_id):
                    deps.bench_jobs.finish(
                        job_id,
                        status="cancelled",
                        summary_json=json.dumps([asdict(o) for o in outcomes_so_far]),
                    )
                    return
                os_for_one = await asyncio.to_thread(
                    partial(
                        run_bench,
                        deps.cfg,
                        qs,
                        repo=deps.bench,
                        strategies=[s_name],
                        corpus_tags=corpus_tags,
                    )
                )
                outcomes_so_far.extend(os_for_one)
                for _ in os_for_one:
                    deps.bench_jobs.increment_progress(job_id)
            deps.bench_jobs.finish(
                job_id,
                status="done",
                summary_json=json.dumps([asdict(o) for o in outcomes_so_far]),
            )
        except Exception as e:
            deps.bench_jobs.finish(
                job_id,
                status="failed",
                summary_json=json.dumps({"error": f"{type(e).__name__}: {e}"}),
            )

    @router.get("/bench/jobs")
    def list_bench_jobs(request: Request, limit: int = 20):
        deps = request.app.state.deps
        rows = deps.bench_jobs.list_recent(limit=limit)
        return {"jobs": [_job_row_to_dict(r) for r in rows]}

    @router.get("/bench/jobs/{job_id}")
    def get_bench_job(job_id: int, request: Request):
        deps = request.app.state.deps
        row = deps.bench_jobs.get(job_id)
        if row is None:
            raise HTTPException(404, f"no bench job with id={job_id}")
        return _job_row_to_dict(row)

    @router.post("/bench/jobs/{job_id}/cancel")
    def cancel_bench_job(job_id: int, request: Request):
        deps = request.app.state.deps
        row = deps.bench_jobs.get(job_id)
        if row is None:
            raise HTTPException(404, f"no bench job with id={job_id}")
        deps.bench_jobs.request_cancel(job_id)
        return {"ok": True}

    @router.get("/bench/jobs/{job_id}/stream")
    async def stream_bench_job(job_id: int, request: Request):
        deps = request.app.state.deps
        row = deps.bench_jobs.get(job_id)
        if row is None:
            raise HTTPException(404, f"no bench job with id={job_id}")

        async def _events():
            last_progress = -1
            while True:
                if await request.is_disconnected():
                    return
                r = deps.bench_jobs.get(job_id)
                if r is None:
                    yield "event: gone\ndata: {}\n\n"
                    return
                if r.progress_done != last_progress:
                    data = json.dumps({"done": r.progress_done, "total": r.progress_total})
                    yield "event: progress\ndata: " + data + "\n\n"
                    last_progress = r.progress_done
                if r.status in ("done", "cancelled", "failed") and r.finished_at is not None:
                    summary = r.summary_json or "{}"
                    yield "event: " + r.status + "\ndata: " + summary + "\n\n"
                    return
                await asyncio.sleep(SSE_POLL_INTERVAL_SEC)

        return StreamingResponse(_events(), media_type="text/event-stream")

    def _job_row_to_dict(r):
        return {
            "id": r.id,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "mode": r.mode,
            "status": r.status,
            "progress_done": r.progress_done,
            "progress_total": r.progress_total,
            "summary_json": r.summary_json,
        }

    @router.post("/scrapers/{name}/test_now")
    async def test_scraper_now(name: str, request: Request):
        """Phase 6v.4: run one bench query against a single scraper and
        return the outcome immediately. Uses the scraper's corpus_tags
        to pick a query it should be able to answer (Bengali for
        kindlebangla_curl, PD classic for gutendex, etc.) — falls back
        to the first general-corpus query when the scraper has no
        special tags. The outcome is also recorded in bench_runs so
        success_rate_30d and the sparkline reflect it on next reload.
        """
        from functools import partial

        from endless_library.bench import load_corpus_tags, queries_for_scraper
        from endless_library.scrapers import registry

        deps = request.app.state.deps
        if name not in registry.available():
            raise HTTPException(404, f"unknown scraper: {name}")
        qs, _ = load_queries()
        corpus_tags = load_corpus_tags()
        scoped = queries_for_scraper(qs, name, corpus_tags)
        if not scoped:
            raise HTTPException(
                400,
                f"no bench query in the corpus matches {name}'s corpus tags "
                f"{sorted(corpus_tags.get(name, frozenset()))}",
            )
        pick = [scoped[0]]
        outcomes = await asyncio.to_thread(
            partial(
                run_bench,
                deps.cfg,
                pick,
                repo=deps.bench,
                strategies=[name],
                corpus_tags=corpus_tags,
            )
        )
        if not outcomes:
            raise HTTPException(
                500, f"{name} produced no outcome (could not build scraper instance?)"
            )
        return {"outcome": asdict(outcomes[0])}

    @router.post("/scrapers/test_pd_chain")
    async def test_pd_chain(request: Request):
        """Phase 6w.9c: build a PD-chain for a classic query and bench it.

        Uses "Pride and Prejudice" / Austen / is_pd=True as the probe query.
        Returns the ordered chain plus live outcomes so the SPA (or any
        caller) can verify that PD scrapers are promoted correctly.
        """
        from dataclasses import asdict as _asdict
        from functools import partial as _partial

        from endless_library.bench import BenchQuery as _BQ
        from endless_library.bench import load_corpus_tags
        from endless_library.bench import run_bench as _run_bench
        from endless_library.scrapers import registry

        deps = request.app.state.deps
        query = _BQ("Pride and Prejudice", "Austen", "", "en", tags=("en", "pd"))
        chain = registry.pd_aware_order(deps.cfg.scrapers, query_title=query.title, is_pd=True)
        corpus_tags = load_corpus_tags()
        outcomes = await asyncio.to_thread(
            _partial(
                _run_bench,
                deps.cfg,
                [query],
                strategies=chain,
                corpus_tags=corpus_tags,
            )
        )
        return {"chain": chain, "outcomes": [_asdict(o) for o in outcomes]}

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

        # bookorbit-upgrade perms guard. /app/deploy/compose.yml is
        # bind-mounted from the host and the in-app upgrade flow
        # writes to it. Two prior outages were caused by the host
        # file losing its group-write bit; flag it loud here so it
        # can't happen silently a third time. Only checked when the
        # file exists (i.e. on the container; pytest tmp dirs skip).
        try:
            _compose_path = Path("/app/deploy/compose.yml")
            if _compose_path.exists():
                if os.access(_compose_path, os.W_OK):
                    components["deploy_compose_writable"] = True
                else:
                    components["deploy_compose_writable"] = False
                    ok = False
        except Exception as e:  # pragma: no cover
            components["deploy_compose_writable"] = f"unknown: {type(e).__name__}"

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

        body: dict = {"ok": ok, **components}
        # Phase 6w.9d: Open Slum upstream status
        slum = getattr(request.app.state, "open_slum_monitor", None)
        if slum is not None:
            external: dict = {}
            for _sn, _site in _SCRAPER_TO_SITE.items():
                if _site not in external:
                    st = slum.get(_site)
                    if st is not None:
                        external[_site] = st
            if external:
                body["external_sources"] = external
        # Phase STK 10: STK delivery health
        try:
            stk_svc = KindleStkService(deps.bookorbit_service)
            if stk_svc.is_configured():
                if deps.cfg.stk.daily_cap is not None:
                    from endless_library.stk_rate import quota_status as _stk_qs

                    qs = _stk_qs(deps.db_path, daily_cap=deps.cfg.stk.daily_cap)
                    body["stk"] = {
                        "configured": True,
                        "sent_24h": qs.sent_24h,
                        "cap": qs.cap,
                        "remaining": qs.remaining,
                        "exhausted": qs.exhausted,
                    }
                else:
                    body["stk"] = {
                        "configured": True,
                        "cap": None,
                        "exhausted": False,
                    }
            else:
                body["stk"] = {"configured": False}
        except Exception:  # pragma: no cover
            body["stk"] = {"configured": False}

        # bookorbit reachability. BookOrbit is the library + upgrade
        # surface; biblichor depends on it for `Add to library` drops,
        # the in-app upgrade flow, and metadata enrichment. When BO
        # is down, biblichor's healthz historically stayed green because
        # this dep was never probed (see 2026-05-25 deploy/.env outage).
        # Only check when bookorbit is enabled in config; tight timeout
        # so a stalled BO can't slow healthz.
        try:
            _bo_cfg = getattr(deps.cfg, "bookorbit", None)
            if (
                _bo_cfg is not None
                and getattr(_bo_cfg, "enabled", False)
                and getattr(_bo_cfg, "url", "")
            ):
                import urllib.error as _urlerr
                import urllib.request as _urlreq

                _url = str(_bo_cfg.url).rstrip("/") + "/api/v1/health"
                try:
                    with _urlreq.urlopen(_url, timeout=2.0) as _resp:
                        if 200 <= _resp.status < 300:
                            body["bookorbit"] = {"reachable": True, "url": _url}
                        else:
                            body["bookorbit"] = {
                                "reachable": False,
                                "url": _url,
                                "error": f"http {_resp.status}",
                            }
                            body["ok"] = False
                            ok = False
                except (_urlerr.URLError, TimeoutError, OSError) as _e:
                    body["bookorbit"] = {
                        "reachable": False,
                        "url": _url,
                        "error": f"{type(_e).__name__}: {_e}",
                    }
                    body["ok"] = False
                    ok = False
            else:
                body["bookorbit"] = {"reachable": None, "reason": "not configured"}
        except Exception as _e:  # pragma: no cover
            body["bookorbit"] = {"reachable": None, "error": f"{type(_e).__name__}"}

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

    @router.post("/scrapers/mobilism/creds")
    def mobilism_store_creds(payload: _MobilismCredsPayload, request: Request):
        """Phase 6w.5d: store Mobilism forum credentials in the encrypted
        secrets store. SPA Scrapers page card uses this.

        Uses set_secret_values for atomic rotation: both username and
        password are written in a single sqlite transaction so the store
        is never left with new username + old password (ultrareview I13).
        """
        svc = _bookorbit_service(request)
        svc.set_secret_values(
            {
                "mobilism.username": payload.username,
                "mobilism.password": payload.password,
            }
        )
        return {"ok": True}

    @router.delete("/scrapers/mobilism/creds")
    def mobilism_clear_creds(request: Request):
        """Phase 6w.5d: clear stored Mobilism credentials."""
        svc = _bookorbit_service(request)
        svc.delete_secret_value("mobilism.username")
        svc.delete_secret_value("mobilism.password")
        return {"ok": True}

    @router.post("/scrapers/mobilism/test-creds")
    def mobilism_test_creds(payload: _MobilismCredsPayload, request: Request):
        """Phase 6w.5d: verify Mobilism credentials by attempting login.

        Uses MobilismSession.try_login() which builds a throw-away client and
        never touches the class-level singleton, so concurrent requests cannot
        accidentally pick up a test-credential session (ultrareview I1).
        """
        from types import SimpleNamespace

        from endless_library.scrapers.mobilism import MobilismSession

        cfg_stub = SimpleNamespace(
            mobilism_username=payload.username,
            mobilism_password=payload.password,
        )
        ok, err = MobilismSession.try_login(cfg_stub)
        if ok:
            return {"ok": True, "message": "Login successful"}
        # Decide HTTP status from the error text
        if err and "not configured" in err.lower():
            raise HTTPException(400, err)
        raise HTTPException(401, err or "Login failed")

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

    # ---------- BookOrbit upgrade (Phase 6v.1) ----------
    # State pattern: the last successful preflight's token + expiry +
    # target version are kept in app.state.upgrade_state so a follow-
    # up POST /upgrade/apply can verify them. Restarting biblichor
    # invalidates the token, which is the safest default — we'd rather
    # force a re-preflight than let a stale token through.
    def _upgrade_state(app) -> dict:
        if not hasattr(app.state, "upgrade_state"):
            app.state.upgrade_state = {}
        return app.state.upgrade_state

    @router.get("/bookorbit/upgrade/status")
    async def bookorbit_upgrade_status(request: Request):
        """Current version, latest version, whether the docker socket
        is reachable, and the release notes for the latest. Used by the
        SPA to render the upgrade card."""
        from functools import partial

        from endless_library.bookorbit.upgrade import get_version_info

        deps = request.app.state.deps
        svc = _bookorbit_service(request)
        creds = svc.get_admin_creds() or (None, None)
        info = await asyncio.to_thread(
            partial(
                get_version_info,
                deps.cfg.bookorbit.url or "",
                admin_username=creds[0],
                admin_password=creds[1],
            )
        )
        state = _upgrade_state(request.app)
        return {
            **asdict(info),
            "has_pending_preflight": bool(state.get("token")),
            "preflight_target": state.get("target_version"),
            "preflight_expires_at": state.get("expires_at"),
        }

    @router.post("/bookorbit/upgrade/preflight")
    async def bookorbit_upgrade_preflight(payload: dict, request: Request):
        """Pull the target image, scan release notes, check disk + DB.
        Stores the resulting token in app.state so Apply can verify it.
        """
        from functools import partial

        from endless_library.bookorbit.upgrade import get_version_info, preflight

        target = (payload or {}).get("target_version") or ""
        if not target:
            raise HTTPException(400, "missing target_version")
        try:
            from endless_library.bookorbit.upgrade import _validate_target_version

            _validate_target_version(target)
        except ValueError as e:
            raise HTTPException(400, str(e))  # noqa: B904

        deps = request.app.state.deps
        svc = _bookorbit_service(request)
        creds = svc.get_admin_creds() or (None, None)
        info = await asyncio.to_thread(
            partial(
                get_version_info,
                deps.cfg.bookorbit.url or "",
                admin_username=creds[0],
                admin_password=creds[1],
            )
        )
        report = await asyncio.to_thread(
            partial(preflight, target, release_notes=info.release_notes)
        )

        state = _upgrade_state(request.app)
        if report.ok:
            state.update(
                {
                    "token": report.token,
                    "target_version": report.target_version,
                    "expires_at": report.expires_at,
                }
            )
        else:
            # Clear any stale token from a previous green preflight so
            # a failing one can't be 'overwritten' by an old success.
            state.clear()
        return report.as_dict()

    @router.post("/bookorbit/upgrade/apply")
    async def bookorbit_upgrade_apply(payload: dict, request: Request):
        """Execute the upgrade. Requires a valid preflight token.
        Returns the full ApplyResult once the upgrade either completes
        or rolls back; the SPA polls /status to refresh the version
        chip after.
        """
        # Serialize concurrent upgrade requests. Two simultaneous POSTs
        # would both pass the expires_at check and both start docker
        # compose ops — the lock prevents that.
        lock = request.app.state.bookorbit_upgrade_lock
        if lock.locked():
            raise HTTPException(409, "another bookorbit upgrade is already in progress")
        async with lock:
            from functools import partial

            from endless_library.bookorbit.upgrade import (
                BOOKORBIT_CONTAINER,
                apply_upgrade,
            )

            deps = request.app.state.deps
            target = (payload or {}).get("target_version") or ""
            submitted_token = (payload or {}).get("token") or ""
            if not target or not submitted_token:
                raise HTTPException(400, "missing target_version and/or token")
            try:
                from endless_library.bookorbit.upgrade import _validate_target_version

                _validate_target_version(target)
            except ValueError as e:
                raise HTTPException(400, str(e))  # noqa: B904

            state = _upgrade_state(request.app)
            expected_token = state.get("token", "")
            expected_target = state.get("target_version", "")
            if target != expected_target:
                raise HTTPException(
                    400,
                    f"target {target} does not match the most recent preflight "
                    f"({expected_target or 'none'}) — run preflight first",
                )

            # Resolve compose + env file paths. The biblichor container
            # bind-mounts /app/deploy from <repo>/deploy at build time.
            compose_path = Path("/app/deploy/compose.yml")
            env_file = Path("/app/.env")
            if not compose_path.exists():
                # Dev/host fallback — the SPA might be running outside the
                # container in pytest.
                from endless_library import __file__ as pkg_root

                repo_root = Path(pkg_root).resolve().parent.parent.parent
                compose_path = repo_root / "deploy" / "compose.yml"
                env_file = repo_root / ".env"

            result = await asyncio.to_thread(
                partial(
                    apply_upgrade,
                    target,
                    submitted_token=submitted_token,
                    expected_token=expected_token,
                    preflight_expires_at=float(state.get("expires_at", 0)),
                    compose_path=compose_path,
                    env_file=env_file,
                    container=BOOKORBIT_CONTAINER,
                    bookorbit_url=deps.cfg.bookorbit.url or "http://bookorbit:3000",
                )
            )

            # On success the token is consumed (don't let it be reused).
            if result.success or result.rolled_back:
                state.clear()
            return result.as_dict()

    @router.post("/bookorbit/setup-token")
    def bookorbit_generate_setup_token():
        """Return a fresh 48-byte URL-safe setup token. Used by the
        SPA wizard when the operator doesn't have BOOKORBIT_SETUP_TOKEN
        in their .env yet."""
        from endless_library.bookorbit.service import BookOrbitService

        return {"token": BookOrbitService.generate_setup_token()}

    # Kindle Send-to-Kindle (Phase STK 9) --------------------------------

    _KNOWN_AMAZON_DOMAINS = {
        "amazon.com",
        "amazon.in",
        "amazon.co.uk",
        "amazon.de",
        "amazon.fr",
        "amazon.it",
        "amazon.es",
        "amazon.co.jp",
        "amazon.com.au",
        "amazon.ca",
        "amazon.com.br",
        "amazon.com.mx",
    }

    @router.get("/kindle-stk/status")
    def kindle_stk_status(request: Request) -> dict:
        deps = request.app.state.deps
        svc = KindleStkService(deps.bookorbit_service)
        amazon_domain = (
            deps.bookorbit_service.get_secret_value("kindle_stk.amazon_domain")
            or getattr(deps.cfg.stk, "amazon_domain", "amazon.com")
            or "amazon.com"
        )
        if not svc.is_configured():
            return {"configured": False, "amazon_domain": amazon_domain}
        return {
            "configured": True,
            "customer_id": deps.bookorbit_service.get_secret_value("kindle_stk.amazon_customer_id"),
            "registered_at": deps.bookorbit_service.get_secret_value("kindle_stk.registered_at"),
            "default_destination": deps.bookorbit_service.get_secret_value(
                "kindle_stk.default_destination_name"
            ),
            "default_destination_sn": deps.bookorbit_service.get_secret_value(
                "kindle_stk.default_destination_sn"
            ),
            "amazon_domain": amazon_domain,
        }

    @router.put("/kindle-stk/region")
    def kindle_stk_set_region(payload: dict, request: Request) -> dict:
        """Persist the user's Amazon regional domain before OAuth setup.

        Must be called before POST /kindle-stk/oauth/start so the sign-in
        URL and token exchange use the correct regional endpoint.
        """
        domain = (payload or {}).get("amazon_domain", "").strip()
        if domain not in _KNOWN_AMAZON_DOMAINS:
            raise HTTPException(400, f"unsupported Amazon domain: {domain!r}")
        deps = request.app.state.deps
        deps.bookorbit_service.set_secret_value("kindle_stk.amazon_domain", domain)
        return {"ok": True, "amazon_domain": domain}

    @router.post("/kindle-stk/oauth/start")
    def kindle_stk_oauth_start(request: Request) -> dict:
        deps = request.app.state.deps
        svc = KindleStkService(deps.bookorbit_service)
        url, _ = svc.start_oauth()
        return {"authorize_url": url}

    @router.post("/kindle-stk/oauth/complete")
    def kindle_stk_oauth_complete(payload: dict, request: Request) -> dict:
        deps = request.app.state.deps
        redirect_url = (payload or {}).get("redirect_url", "")
        if not redirect_url:
            raise HTTPException(400, "redirect_url is required")
        svc = KindleStkService(deps.bookorbit_service)
        try:
            return svc.complete_oauth(redirect_url)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except KindleStkUploadFailed as e:
            raise HTTPException(502, str(e)) from e

    def _derive_device_type(caps) -> str:
        """Real OwnedDevice has no device_type field — only a capabilities
        mapping. Derive a friendly type-like string for the SPA's
        Kindle-for-Web pre-selection logic. Capability key names vary
        across device families; we check several known markers and fall
        back to 'kindle'."""
        if not caps:
            return "kindle"
        c = {str(k).lower(): bool(v) for k, v in dict(caps).items() if v}
        for marker, label in (
            ("supports_web_send", "FionaWebApp"),
            ("web_app", "FionaWebApp"),
            ("cloudreader", "FionaWebApp"),
            ("kindle_reader", "FionaWebApp"),
            ("supports_iphone_send", "iOS"),
            ("supports_android_send", "Android"),
        ):
            if marker in c:
                return label
        return "kindle"

    @router.get("/kindle-stk/devices")
    def kindle_stk_devices(request: Request) -> dict:
        deps = request.app.state.deps
        svc = KindleStkService(deps.bookorbit_service)
        try:
            devs = svc.list_devices()
        except KindleStkNotConfigured as e:
            raise HTTPException(400, str(e)) from e
        return {
            "devices": [
                {
                    "device_serial_number": d.device_serial_number,
                    "device_type": getattr(d, "device_type", "")
                    or _derive_device_type(getattr(d, "device_capabilities", {})),
                    "device_name": d.device_name,
                    # Expose raw capabilities so the SPA can do its own
                    # Kindle-for-Web detection without trusting our heuristic.
                    "device_capabilities": dict(getattr(d, "device_capabilities", {})),
                }
                for d in devs
            ]
        }

    @router.put("/kindle-stk/default-destination")
    def kindle_stk_set_destination(payload: dict, request: Request) -> dict:
        deps = request.app.state.deps
        sn = (payload or {}).get("device_sn", "")
        if not sn:
            raise HTTPException(400, "device_sn is required")
        svc = KindleStkService(deps.bookorbit_service)
        try:
            svc.set_default_destination(sn)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except KindleStkNotConfigured as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @router.post("/kindle-stk/test-send")
    def kindle_stk_test_send(request: Request) -> dict:
        """Send a tiny test file to verify the configured device works."""
        import tempfile

        deps = request.app.state.deps
        svc = KindleStkService(deps.bookorbit_service)
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("biblichor connection test -- Phase STK 9.")
            tmp = Path(f.name)
        try:
            result = svc.send_file(tmp, format="TXT", title="biblichor test", author="biblichor")
            return {"ok": True, "result": result}
        except KindleStkNotConfigured as e:
            raise HTTPException(400, str(e)) from e
        except KindleStkAuthExpired as e:
            raise HTTPException(401, str(e)) from e
        except KindleStkRateLimited as e:
            raise HTTPException(429, str(e), headers={"Retry-After": str(e.retry_after_sec)}) from e
        except KindleStkUploadFailed as e:
            raise HTTPException(502, str(e)) from e
        finally:
            tmp.unlink(missing_ok=True)

    @router.delete("/kindle-stk/connection")
    def kindle_stk_deregister(request: Request) -> dict:
        deps = request.app.state.deps
        svc = KindleStkService(deps.bookorbit_service)
        svc.deregister()
        return {"ok": True}

    # ---------- search ----------

    @router.get("/search")
    def search(request: Request, q: str, limit: int = 20, lang: str = "", sources: str = ""):
        """Multi-scraper fan-out search.

        Query params:
          q       — search title text (required, 3+ chars)
          limit   — max results to return (1..50, default 20)
          lang    — override language: "en"/"bn"/etc, or "all" to skip
                    filtering. Default: cfg.scrapers.language.
          sources — comma-separated scraper names to query. Default:
                    every enabled scraper from cfg.scrapers.

        Behavior:
          - All selected scrapers run in parallel (ThreadPoolExecutor).
          - Per-scraper timeout 8s; timeouts/errors are skipped, not raised.
          - Results deduped by md5 (first hit wins).
          - Response includes sources_used + sources_skipped so the UI
            can show which sources contributed.
        """
        import concurrent.futures as _cf

        from endless_library.domain.models import SearchQuery
        from endless_library.scrapers import registry as _reg

        q_clean = (q or "").strip()
        if not q_clean:
            raise HTTPException(400, "query parameter 'q' is required")
        if len(q_clean) < 3:
            raise HTTPException(400, "query must be at least 3 characters")

        deps = request.app.state.deps
        # Interactive search uses a no-delay copy of the scrapers config.
        # The pipeline-side cfg has request_delay_seconds=6 (polite for
        # batch processing 1000 books); applying that to per-keystroke
        # search means 7-8s of pure sleep before any HTTP. The token
        # bucket on the scraper instance still enforces a saner ceiling
        # (default 6/minute), so this isn't an abuse vector.
        if hasattr(deps.cfg.scrapers, "model_copy"):
            sc = deps.cfg.scrapers.model_copy(update={"request_delay_seconds": 0.0})
        else:
            # SimpleNamespace test stub — mutate a copy via copy.copy()
            import copy as _copy

            sc = _copy.copy(deps.cfg.scrapers)
            sc.request_delay_seconds = 0.0

        # Resolve language. "all" / "" via the param means: skip language
        # filtering at the scoring stage (the scrapers themselves may still
        # filter via their URL params if they only support one language).
        lang_clean = (lang or "").strip().lower()
        if lang_clean and lang_clean != "all":
            effective_lang = lang_clean
            user_picked_lang = True
        else:
            effective_lang = getattr(sc, "language", "en") or "en"
            user_picked_lang = False

        # Resolve scraper set. Default behaviour: Anna's-only (the dominant
        # source — sub-second response, 80%+ coverage). Multi-source fan-out
        # is opt-in via explicit ?sources=annas_curl,doab,... because every
        # extra scraper adds its own external latency and rate-limit risk.
        # This default reverts the over-eager change from PR #6 which made
        # every keystroke wait ~9s for the slowest of 7 scrapers.
        if sources:
            requested = [s.strip() for s in sources.split(",") if s.strip()]
            scraper_names = [n for n in requested if n in _reg.available()]
        else:
            scraper_names = ["annas_curl"] if "annas_curl" in _reg.available() else []
        # Cap fan-out at 6 even if user requests more.
        scraper_names = scraper_names[:6]
        if not scraper_names:
            raise HTTPException(503, "no scrapers available for this query")

        # Language-aware query augmentation. Annas lang= filter is a soft
        # hint that gets mostly ignored; their full-text index works better
        # when we add the language NAME to the query for non-English picks.
        # "kriya yoga" + bn -> "kriya yoga bengali" returns far more Bengali
        # matches than the lang= filter alone (live tested 2026-05-26).
        _LANG_NAME = {
            "bn": "bengali",
            "hi": "hindi",
            "ta": "tamil",
            "te": "telugu",
            "ml": "malayalam",
            "mr": "marathi",
            "gu": "gujarati",
            "pa": "punjabi",
            "ja": "japanese",
            "ko": "korean",
            "zh": "chinese",
            "ar": "arabic",
            "he": "hebrew",
            "ru": "russian",
            "es": "spanish",
            "fr": "french",
            "de": "german",
            "it": "italian",
            "pt": "portuguese",
        }
        augmented_title = q_clean
        if (
            user_picked_lang
            and effective_lang != "en"
            and effective_lang in _LANG_NAME
            and not any(ord(ch) > 0x024F for ch in q_clean)
        ):
            augmented_title = f"{q_clean} {_LANG_NAME[effective_lang]}"

        query = SearchQuery(
            title=augmented_title,
            author=None,
            isbn13=None,
            # Interactive search hits Anna's once. Pipeline-side scraper
            # iterates the full format ladder (epub->azw3->mobi->pdf), each one
            # an HTTP call -- fine for batch download where you want any format,
            # ruinous for keystroke latency. Limit to the user's top choice;
            # if no epub exists, the SPA can hint that explicitly later.
            format_priority=((getattr(sc, "format_priority", None) or ["epub"])[0],),
            language=effective_lang,
        )

        # Tight per-scraper budget. Anna's typically returns in <1s; 3s gives
        # slower sources a fair shake without blocking the UI.
        per_scraper_timeout = 3.0
        used: list[str] = []
        skipped: list[dict] = []
        merged: list = []

        def _run_one(name: str):
            try:
                sc_inst = _reg.build(name, sc)
            except Exception as e:
                return name, None, f"build failed: {type(e).__name__}: {e}"
            try:
                return name, list(sc_inst.search(query)), None
            except Exception as e:
                return name, None, f"{type(e).__name__}: {e}"

        with _cf.ThreadPoolExecutor(max_workers=min(len(scraper_names), 6)) as ex:
            futures = {ex.submit(_run_one, n): n for n in scraper_names}
            # NOTE: as_completed() itself raises TimeoutError when the outer
            # deadline expires with futures still pending. PR #6 only caught
            # exceptions inside the loop body, so a slow scraper exploded the
            # whole endpoint as a 500. Wrap the iteration to catch the
            # pool-level timeout cleanly.
            try:
                for fut in _cf.as_completed(futures, timeout=per_scraper_timeout + 1):
                    name = futures[fut]
                    try:
                        n, cands, err = fut.result(timeout=0.1)
                    except (_cf.TimeoutError, Exception) as e:
                        skipped.append({"name": name, "reason": f"{type(e).__name__}"})
                        continue
                    if err is not None or cands is None:
                        skipped.append({"name": n, "reason": err or "no results"})
                        continue
                    used.append(n)
                    merged.extend(cands)
            except _cf.TimeoutError:
                # Pool-level timeout: collect anything that DID finish in time
                # and mark the rest as skipped. The user gets fast partial
                # results instead of a 500.
                for fut, name in futures.items():
                    if (
                        fut.done()
                        and name not in used
                        and not any(s["name"] == name for s in skipped)
                    ):
                        try:
                            n, cands, err = fut.result(timeout=0.1)
                            if err is None and cands is not None:
                                used.append(n)
                                merged.extend(cands)
                            else:
                                skipped.append({"name": n, "reason": err or "no results"})
                        except Exception as e:
                            skipped.append({"name": name, "reason": f"{type(e).__name__}"})
                    elif not fut.done():
                        skipped.append({"name": name, "reason": "timeout"})
                        fut.cancel()

        # Dedup by md5 (first hit wins); non-md5 candidates keyed by
        # (provider, detail_url).
        deduped: list = []
        seen_md5: set[str] = set()
        seen_url: set[tuple[str, str]] = set()
        for c in merged:
            if c.md5:
                if c.md5 in seen_md5:
                    continue
                seen_md5.add(c.md5)
            else:
                k = (c.provider, c.detail_url)
                if k in seen_url:
                    continue
                seen_url.add(k)
            deduped.append(c)

        # Language post-filter: trust script presence over Annas often-empty
        # `language` tag. For known non-Latin langs we keep candidates whose
        # title contains at least one glyph from that script. For Latin-script
        # langs we fall back to c.language tag match.
        _LANG_SCRIPT_RANGE = {
            "bn": (0x0980, 0x09FF),
            "hi": (0x0900, 0x097F),
            "mr": (0x0900, 0x097F),
            "ta": (0x0B80, 0x0BFF),
            "te": (0x0C00, 0x0C7F),
            "ml": (0x0D00, 0x0D7F),
            "gu": (0x0A80, 0x0AFF),
            "pa": (0x0A00, 0x0A7F),
            "ja": (0x3040, 0x9FFF),
            "ko": (0xAC00, 0xD7AF),
            "zh": (0x4E00, 0x9FFF),
            "ar": (0x0600, 0x06FF),
            "he": (0x0590, 0x05FF),
            "ru": (0x0400, 0x04FF),
        }
        language_filter_applied = False
        if user_picked_lang and effective_lang != "en":
            script_range = _LANG_SCRIPT_RANGE.get(effective_lang)
            target_lang = effective_lang

            def _matches_lang(cand) -> bool:
                t = cand.title or ""
                if script_range and any(script_range[0] <= ord(ch) <= script_range[1] for ch in t):
                    return True
                cl = (cand.language or "").lower()
                return cl == target_lang or cl.startswith(target_lang)

            filtered_results = [c for c in deduped if _matches_lang(c)]
            if filtered_results:
                deduped = filtered_results
                language_filter_applied = True
            # else: leave deduped as-is so user sees something + SPA hint.

        deduped = deduped[: max(1, min(int(limit), 50))]

        # biblichor books-table cross-ref.
        md5s = [c.md5 for c in deduped if c.md5]
        in_lib: dict[str, dict] = {}
        if md5s:
            with connect(deps.db_path) as conn:
                placeholders = ",".join(["?"] * len(md5s))
                for r in conn.execute(
                    f"SELECT md5, id, status FROM books WHERE md5 IN ({placeholders})",
                    md5s,
                ):
                    in_lib[r["md5"]] = {"id": r["id"], "status": r["status"]}

        results_out = []
        for c in deduped:
            raw = c.raw or {}
            results_out.append(
                {
                    "md5": c.md5,
                    "title": c.title,
                    "author": c.author,
                    "language": c.language,
                    "format": c.format,
                    "filesize_bytes": c.filesize_bytes,
                    "year": c.year,
                    "publisher": c.publisher,
                    "isbn13": raw.get("isbn13"),
                    "cover_url": raw.get("cover_url"),
                    "detail_url": c.detail_url,
                    "provider": c.provider,
                    "in_library": in_lib.get(c.md5) if c.md5 else None,
                }
            )
        return {
            "query": q_clean,
            "lang": effective_lang,
            "language_filter_applied": language_filter_applied,
            "count": len(results_out),
            "results": results_out,
            "sources_used": used,
            "sources_skipped": skipped,
        }

    @router.post("/books/from-search")
    def add_from_search(payload: _AddFromSearchPayload, request: Request):
        """Queue a book picked from a /api/search result.

        Idempotent on md5: existing row -> {created: False, ...}."""
        deps = request.app.state.deps
        with connect(deps.db_path) as conn:
            existing = conn.execute(
                "SELECT id, status FROM books WHERE md5 = ?", (payload.md5,)
            ).fetchone()
            if existing:
                return {
                    "created": False,
                    "book_id": existing["id"],
                    "status": existing["status"],
                    "message": "already tracked",
                }

        book_id = deps.books.upsert(
            title=payload.title,
            author=payload.author or "",
            isbn13=payload.isbn13,
            source="manual",
            source_id=f"search:{payload.md5}",
        )
        cand_id = deps.cands.insert(
            book_id=book_id,
            provider=payload.provider,
            md5=payload.md5,
            title=payload.title,
            author=payload.author,
            language=payload.language,
            format=payload.format,
            filesize_bytes=payload.filesize_bytes,
            year=payload.year,
            publisher=payload.publisher,
            edition_hints="",
            score=100.0,
            detail_url=payload.detail_url,
            raw_json='{"manual_pick": true, "source": "api/search"}',
        )
        with connect(deps.db_path) as conn:
            conn.execute(
                "UPDATE books SET picked_candidate_id = ?, status = 'queued', md5 = ? WHERE id = ?",
                (cand_id, payload.md5, book_id),
            )
            conn.commit()
        return {
            "created": True,
            "book_id": book_id,
            "candidate_id": cand_id,
            "status": "queued",
        }

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

    _register_dashboard(app)


# Dashboard aggregation (Phase dashboard-live)
# =========================================================

DASHBOARD_INTERVAL_SEC: float = 3.0
"""Module-level so tests can monkeypatch:
   monkeypatch.setattr('endless_library.web.api.DASHBOARD_INTERVAL_SEC', 0.05)
"""


def compute_dashboard_snapshot(db_path) -> dict:
    """Pure aggregation - no FastAPI deps, testable standalone."""
    from datetime import datetime, timedelta

    now_utc = datetime.now(UTC)
    cutoff_str = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    with connect(db_path) as conn:
        # 1. Status counts
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM books GROUP BY status").fetchall()
        status_counts: dict = {}
        for r in rows:
            status_counts[str(r["status"])] = int(r["n"])

        # 2. Throughput bucketed to 5-min windows over last 24h
        tp_rows = conn.execute(
            """
            SELECT strftime('%Y-%m-%dT%H:%M:00Z', ts) AS minute, kind, COUNT(*) AS n
            FROM events
            WHERE kind IN ('send-stk', 'send') AND ts >= ?
            GROUP BY 1, 2
            """,
            (cutoff_str,),
        ).fetchall()

        bucket_minutes = 5
        num_buckets = (24 * 60) // bucket_minutes

        def _bucket_label(dt):
            floored = dt.replace(
                minute=(dt.minute // bucket_minutes) * bucket_minutes,
                second=0,
                microsecond=0,
            )
            return floored.strftime("%Y-%m-%dT%H:%M:00Z")

        oldest_bucket = (now_utc - timedelta(hours=24)).replace(second=0, microsecond=0)
        oldest_bucket = oldest_bucket.replace(
            minute=(oldest_bucket.minute // bucket_minutes) * bucket_minutes
        )
        labels = [
            (oldest_bucket + timedelta(minutes=i * bucket_minutes)).strftime("%Y-%m-%dT%H:%M:00Z")
            for i in range(num_buckets + 1)
        ]

        stk_map: dict = {lbl: 0 for lbl in labels}
        smtp_map: dict = {lbl: 0 for lbl in labels}

        for r in tp_rows:
            try:
                from datetime import datetime as _dt2

                dt = _dt2.strptime(str(r["minute"]), "%Y-%m-%dT%H:%M:00Z").replace(tzinfo=UTC)
                bucket = _bucket_label(dt)
            except Exception:
                continue
            if r["kind"] == "send-stk":
                stk_map[bucket] = stk_map.get(bucket, 0) + int(r["n"])
            else:
                smtp_map[bucket] = smtp_map.get(bucket, 0) + int(r["n"])

        throughput_24h = {
            "bucket_minutes": bucket_minutes,
            "series": [
                {"name": "stk", "points": [{"t": lbl, "v": stk_map.get(lbl, 0)} for lbl in labels]},
                {
                    "name": "smtp",
                    "points": [{"t": lbl, "v": smtp_map.get(lbl, 0)} for lbl in labels],
                },
            ],
        }

        # 3. Method breakdown: kindled books in last 24h by sent_method
        mb_rows = conn.execute(
            """
            SELECT sent_method, COUNT(*) AS n
            FROM books
            WHERE status = 'kindled' AND sent_method IS NOT NULL AND sent_at >= ?
            GROUP BY sent_method
            """,
            (cutoff_str,),
        ).fetchall()
        raw_mb: dict = {str(r["sent_method"]): int(r["n"]) for r in mb_rows}
        method_breakdown_24h = {
            "stk": raw_mb.get("stk", raw_mb.get("send-stk", 0)),
            "smtp": raw_mb.get("smtp", raw_mb.get("send", 0)),
        }

        # 4. Source funnel
        sf_rows = conn.execute(
            """
            SELECT source,
                COUNT(*) AS discovered,
                SUM(CASE WHEN file_path IS NOT NULL THEN 1 ELSE 0 END) AS downloaded,
                SUM(CASE WHEN status IN ('sent', 'kindled') THEN 1 ELSE 0 END) AS sent
            FROM books
            GROUP BY source
            ORDER BY discovered DESC
            """,
        ).fetchall()
        source_funnel = [
            {
                "source": str(r["source"]),
                "discovered": int(r["discovered"]),
                "downloaded": int(r["downloaded"]),
                "sent": int(r["sent"]),
            }
            for r in sf_rows
        ]

    return {
        "ts": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status_counts": status_counts,
        "throughput_24h": throughput_24h,
        "method_breakdown_24h": method_breakdown_24h,
        "source_funnel": source_funnel,
    }


def _register_dashboard(app) -> None:
    """Register /api/dashboard/* endpoints on an already-built app."""
    _router = APIRouter(prefix="/api")

    @_router.get("/dashboard/snapshot")
    def dashboard_snapshot(request: Request):
        deps = request.app.state.deps
        return compute_dashboard_snapshot(deps.db_path)

    @_router.get("/dashboard/stream")
    async def dashboard_stream(request: Request):
        deps = request.app.state.deps

        async def _gen():
            try:
                while True:
                    snapshot = compute_dashboard_snapshot(deps.db_path)
                    yield "data: " + json.dumps(snapshot) + chr(10) + chr(10)
                    await asyncio.sleep(DASHBOARD_INTERVAL_SEC)
                    if await request.is_disconnected():
                        return
            except asyncio.CancelledError:
                return

        return StreamingResponse(_gen(), media_type="text/event-stream")

    app.include_router(_router)
