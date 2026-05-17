"""JSON API for the SPA. Replaces the old HTML-rendering routes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from endless_library.bench import format_table, load_queries, run_bench
from endless_library.config import save_config
from endless_library.db.schema import connect

log = logging.getLogger(__name__)

# ---------- pydantic models for inbound payloads ----------


class AddBook(BaseModel):
    title: str
    author: str | None = None
    isbn13: str | None = None


class AddSource(BaseModel):
    source: str
    identifier: str
    token: str | None = None
    poll_interval_minutes: int = 60


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
    pushover_enabled: bool | None = None
    pushover_user_key: str | None = None
    pushover_app_token: str | None = None


def register(app: FastAPI) -> None:
    router = APIRouter(prefix="/api")

    # ---------- queue + books ----------

    @router.get("/books")
    def list_books(
        request: Request, status: str | None = None, q: str | None = None, limit: int = 500
    ):
        deps = request.app.state.deps
        with connect(deps.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM books ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if status and d["status"] != status:
                continue
            if q:
                qlc = q.lower()
                if qlc not in (d["title"] or "").lower() and qlc not in (d["author"] or "").lower():
                    continue
            out.append(d)
        return {"books": out, "total": len(out)}

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
        deps = request.app.state.deps
        book = deps.books.get(book_id)
        if not book:
            raise HTTPException(404)
        candidates = [asdict(c) for c in deps.cands.top_for_book(book_id, limit=10)]
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
        deps = request.app.state.deps
        if not deps.books.get(book_id):
            raise HTTPException(404)
        deps.books.set_status(book_id, "queued", error=None)
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

    # ---------- sources ----------

    @router.get("/sources")
    def list_sources(request: Request):
        deps = request.app.state.deps
        return {"sources": [asdict(s) for s in deps.sources.list_all()]}

    @router.post("/sources")
    def add_source(payload: AddSource, request: Request):
        deps = request.app.state.deps
        if payload.source not in (
            "goodreads",
            "hardcover",
            "manual",
            "goodreads_listopia",
            "goodreads_series",
        ):
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
        deps.sources.set_enabled(sid, not row.enabled)
        return {"ok": True, "enabled": not row.enabled}

    @router.post("/sources/{sid}/delete")
    def delete_source(sid: int, request: Request):
        deps = request.app.state.deps
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

    @router.post("/bench/run")
    async def run_bench_endpoint(request: Request, mode: str = "quick"):
        deps = request.app.state.deps
        bench_path = Path("bench/queries.yaml")
        qs, quick_idx = load_queries(bench_path)
        if mode == "quick":
            qs = [qs[i] for i in quick_idx if i < len(qs)]
        outcomes = await asyncio.to_thread(run_bench, deps.cfg, qs, deps.bench)
        return {
            "outcomes": [asdict(o) for o in outcomes],
            "table": format_table(outcomes),
        }

    # ---------- settings ----------

    @router.get("/settings")
    def get_settings(request: Request):
        deps = request.app.state.deps
        return deps.cfg.public_view()

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

    # ---------- healthz ----------

    @router.get("/healthz")
    def healthz(request: Request):
        deps = request.app.state.deps
        queue = len(deps.books.pending(max_attempts=10_000))
        try:
            db_size = Path(deps.db_path).stat().st_size
        except Exception:
            db_size = 0
        return {"ok": True, "queue_size": queue, "db_size_bytes": db_size}

    # ---------- mirrors ----------

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
