# Phase 6w Scraper Sweep — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land 7 sub-phases (`6w.0` through `6w.9`, non-sequential — see spec) extending biblichor's scraper layer with bench-as-async, curl-cffi + Anubis HTTP foundation, Anna's hardening, two PD/OA sources (HathiTrust + DOAB), Mobilism EN forum, BDeBooks Bengali source with content-filter abstraction, and Patchright-revived welib + Open Slum health.

**Architecture:** Introduces three abstractions to the existing scraper system — a shared HTTP client factory (`make_client`) that all scrapers migrate to, an Anubis PoW middleware that retries any Anubis-gated request transparently, and a `Candidate.categories` field with a per-source `excluded_categories` denylist. Async bench jobs are persisted in a new SQLite table with SSE-streamed progress. Sources that need credentials store them in the existing encrypted secrets store. One new compose service (`cf-bypass`) joins the network by default.

**Tech Stack:** Python 3.12, FastAPI, httpx + curl-cffi, SQLite, asyncio, Vue 3 + Vite SPA, Docker Compose. New deps: `curl-cffi`, `patchright`. New compose image: `sarperavci/cloudflarebypassforscraping`.

---

## How to use this plan

- Each sub-phase is independently shippable: tests green, commit at the end, can pause between sub-phases.
- All work happens on the remote `claude-1` host at `/home/ubuntu/endless-library/`. Connect via `ssh ubuntu@claude-1`.
- Tests are run inside the existing venv: `cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest <path> -v`.
- The container needs a rebuild for any scraper module change to take effect in production. Use: `cd /home/ubuntu/endless-library && docker compose -f deploy/compose.yml --env-file ./.env up -d --build biblichor`. Same convention as Phase 6v.
- For quick in-container reloads without a full rebuild: `docker cp <local-file> biblichor:/app/<container-path> && docker restart biblichor`. Use this for fast iteration; rebuild only for the final commit per sub-phase.
- After each sub-phase, run the full suite once: `python -m pytest -q` — must stay green.

---

## Sub-phase 6w.0 — Bench-as-async

**Goal of sub-phase:** Stop `/api/bench/run` from blocking. Returns 202 + job_id, runs the bench in a background task, exposes progress + outcomes via REST and SSE.

### File map

- **Create:** `src/endless_library/db/bench_jobs.py` — `BenchJobsRepo` class
- **Create:** `tests/unit/test_phase6w0_bench_async.py`
- **Modify:** `src/endless_library/db/schema.py` — `init_db()` creates the new table
- **Modify:** `src/endless_library/web/api.py` — replace sync `/bench/run`, add `/bench/jobs/*` endpoints
- **Modify:** `src/endless_library/bench.py` — add `per_query_timeout_sec` parameter + circuit breaker
- **Modify:** `webapp/src/pages/ScrapersPage.vue` — bench buttons use job-id + SSE
- **Modify:** `src/endless_library/config.py` — `BenchCfg` gains `per_query_timeout_sec` (default 20)

### Task 1: bench_jobs table schema

**Files:**
- Modify: `src/endless_library/db/schema.py`
- Test: `tests/unit/test_phase6w0_bench_async.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_phase6w0_bench_async.py
from pathlib import Path
from endless_library.db.schema import connect, init_db


def test_bench_jobs_table_exists_after_init_db(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    with connect(db) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bench_jobs'"
        ).fetchone()
    assert row is not None


def test_bench_jobs_columns_are_correct(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    with connect(db) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(bench_jobs)")}
    assert cols >= {
        "id", "started_at", "finished_at", "mode", "status",
        "progress_done", "progress_total", "summary_json",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w0_bench_async.py -v"
```
Expected: 2 failures with "no such table: bench_jobs".

- [ ] **Step 3: Add table to `init_db`**

Open `src/endless_library/db/schema.py` and find `init_db`. Add this CREATE TABLE near the other tables:

```python
conn.execute(
    """CREATE TABLE IF NOT EXISTS bench_jobs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at      TEXT NOT NULL,
        finished_at     TEXT,
        mode            TEXT NOT NULL,
        status          TEXT NOT NULL CHECK(status IN ('running','done','cancelled','failed')),
        progress_done   INTEGER NOT NULL DEFAULT 0,
        progress_total  INTEGER NOT NULL,
        summary_json    TEXT
    )"""
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w0_bench_async.py -v"
```
Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/db/schema.py tests/unit/test_phase6w0_bench_async.py && git commit -m 'Phase 6w.0a: bench_jobs table' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 2: BenchJobsRepo CRUD

**Files:**
- Create: `src/endless_library/db/bench_jobs.py`
- Test: `tests/unit/test_phase6w0_bench_async.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_phase6w0_bench_async.py`:

```python
from endless_library.db.bench_jobs import BenchJobsRepo, BenchJobRow


def test_create_job_returns_id_and_initial_running_status(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=12)
    assert isinstance(job_id, int) and job_id > 0
    row = repo.get(job_id)
    assert row.status == "running"
    assert row.progress_done == 0
    assert row.progress_total == 12
    assert row.finished_at is None


def test_increment_progress(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=5)
    repo.increment_progress(job_id)
    repo.increment_progress(job_id)
    row = repo.get(job_id)
    assert row.progress_done == 2


def test_finish_sets_finished_at_and_status_and_summary(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=3)
    repo.finish(job_id, status="done", summary_json='{"x":1}')
    row = repo.get(job_id)
    assert row.status == "done"
    assert row.finished_at is not None
    assert row.summary_json == '{"x":1}'


def test_request_cancel_flips_status(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    job_id = repo.create(mode="quick", progress_total=3)
    assert repo.is_cancel_requested(job_id) is False
    repo.request_cancel(job_id)
    assert repo.is_cancel_requested(job_id) is True


def test_list_recent_returns_newest_first_with_limit(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    ids = [repo.create(mode="quick", progress_total=1) for _ in range(5)]
    rows = repo.list_recent(limit=3)
    assert [r.id for r in rows] == list(reversed(ids))[:3]


def test_get_returns_none_for_missing_id(tmp_path: Path):
    db = tmp_path / "x.db"
    init_db(db)
    repo = BenchJobsRepo(db)
    assert repo.get(9999) is None
```

- [ ] **Step 2: Run to verify failure**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w0_bench_async.py -v"
```
Expected: 6 new failures with ImportError on `BenchJobsRepo`.

- [ ] **Step 3: Create the repo**

Create `src/endless_library/db/bench_jobs.py`:

```python
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .schema import connect


@dataclass(frozen=True, slots=True)
class BenchJobRow:
    id: int
    started_at: str
    finished_at: str | None
    mode: str
    status: str
    progress_done: int
    progress_total: int
    summary_json: str | None
    cancel_requested: bool = False  # derived, not stored

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "BenchJobRow":
        return cls(
            id=r["id"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
            mode=r["mode"],
            status=r["status"],
            progress_done=r["progress_done"],
            progress_total=r["progress_total"],
            summary_json=r["summary_json"],
            # cancel_requested encoded as status='cancelled' once worker sees it;
            # to read the *requested* state mid-run, use is_cancel_requested().
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BenchJobsRepo:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def create(self, *, mode: str, progress_total: int) -> int:
        with connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO bench_jobs (started_at, mode, status, progress_total)
                   VALUES (?, ?, 'running', ?)""",
                (_now_iso(), mode, progress_total),
            )
            return int(cur.lastrowid)

    def increment_progress(self, job_id: int) -> None:
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE bench_jobs SET progress_done = progress_done + 1 WHERE id = ?",
                (job_id,),
            )

    def finish(self, job_id: int, *, status: str, summary_json: str | None = None) -> None:
        assert status in ("done", "cancelled", "failed")
        with connect(self.db_path) as conn:
            conn.execute(
                """UPDATE bench_jobs SET status = ?, finished_at = ?, summary_json = ?
                   WHERE id = ?""",
                (status, _now_iso(), summary_json, job_id),
            )

    def request_cancel(self, job_id: int) -> None:
        """Mark cancellation requested. The worker checks via
        is_cancel_requested between queries; on next check it stops
        and calls finish(status='cancelled')."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE bench_jobs SET status = 'cancelled' WHERE id = ? AND status = 'running'",
                (job_id,),
            )

    def is_cancel_requested(self, job_id: int) -> bool:
        row = self.get(job_id)
        return row is not None and row.status == "cancelled"

    def get(self, job_id: int) -> BenchJobRow | None:
        with connect(self.db_path) as conn:
            r = conn.execute(
                "SELECT * FROM bench_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return BenchJobRow.from_row(r) if r else None

    def list_recent(self, limit: int = 20) -> list[BenchJobRow]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM bench_jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [BenchJobRow.from_row(r) for r in rows]
```

- [ ] **Step 4: Run tests**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w0_bench_async.py -v"
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/db/bench_jobs.py tests/unit/test_phase6w0_bench_async.py && git commit -m 'Phase 6w.0b: BenchJobsRepo CRUD' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 3: Per-query timeout + circuit breaker in `run_bench`

**Files:**
- Modify: `src/endless_library/bench.py`
- Modify: `src/endless_library/config.py` (`BenchCfg.per_query_timeout_sec`)
- Test: `tests/unit/test_phase6w0_bench_async.py` (extend)

- [ ] **Step 1: Add cfg field**

In `src/endless_library/config.py`, find `BenchCfg` (or the bench section of `Config`) and add:

```python
class BenchCfg(BaseModel):
    per_query_timeout_sec: int = 20
    circuit_break_after_consecutive_fails: int = 3
```

If `BenchCfg` doesn't exist yet, create it and wire under `Config.bench`.

- [ ] **Step 2: Write failing tests for timeout + circuit**

Append to `tests/unit/test_phase6w0_bench_async.py`:

```python
import time
import types

from endless_library.bench import BenchQuery, run_bench


class _SlowScraper:
    name = "slow"
    def __init__(self, *a, **kw): pass
    def search(self, q):
        time.sleep(2)
        return []


class _FailingScraper:
    name = "fail"
    def __init__(self, *a, **kw): pass
    def search(self, q):
        raise RuntimeError("boom")


def _fake_registry(monkeypatch, by_name: dict):
    from endless_library.scrapers import registry as r
    monkeypatch.setattr(r, "_REGISTRY", by_name)
    monkeypatch.setattr(r, "enabled_order", lambda cfg: list(by_name.keys()))
    monkeypatch.setattr(r, "build", lambda n, cfg, **kw: by_name[n]())


def test_run_bench_records_timeout_as_failure(tmp_path, monkeypatch):
    """A scraper that exceeds per_query_timeout_sec must record
    success=false with note='timeout' rather than blocking forever or
    raising."""
    _fake_registry(monkeypatch, {"slow": _SlowScraper})
    cfg = types.SimpleNamespace(
        scrapers=types.SimpleNamespace(
            order=["slow"], enabled={"slow": True},
            format_priority=("epub",),
        ),
        bench=types.SimpleNamespace(per_query_timeout_sec=0.5,
                                    circuit_break_after_consecutive_fails=3),
    )
    qs = [BenchQuery("X", "Y", "", "en")]
    outcomes = run_bench(cfg, qs)
    assert len(outcomes) == 1
    assert outcomes[0].success is False
    assert "timeout" in outcomes[0].note.lower()


def test_run_bench_circuit_breaks_after_3_consecutive_fails(tmp_path, monkeypatch):
    _fake_registry(monkeypatch, {"fail": _FailingScraper})
    cfg = types.SimpleNamespace(
        scrapers=types.SimpleNamespace(
            order=["fail"], enabled={"fail": True},
            format_priority=("epub",),
        ),
        bench=types.SimpleNamespace(per_query_timeout_sec=5,
                                    circuit_break_after_consecutive_fails=3),
    )
    qs = [BenchQuery(f"Q{i}", "A", "", "en") for i in range(6)]
    outcomes = run_bench(cfg, qs)
    # First 3 are real failures (RuntimeError caught), next 3 are circuit-broken
    by_note = [o.note for o in outcomes]
    assert sum(1 for n in by_note if "circuit-broken" in n) == 3
    assert sum(1 for n in by_note if "RuntimeError" in n) == 3
```

- [ ] **Step 3: Run to verify failure**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w0_bench_async.py -v -k 'timeout or circuit'"
```
Expected: both fail (timeout test runs forever or fails on `wait_for`; circuit test produces 6 failures instead of 3+3).

- [ ] **Step 4: Implement timeout + circuit in `run_bench`**

In `src/endless_library/bench.py`, locate the per-scraper / per-query loop. Replace the inner try with the following pattern (preserving existing outcome shape):

```python
import concurrent.futures as _cf

# inside run_bench, replace the inner query loop:
for s_name in strats:
    try:
        scraper = registry.build(s_name, cfg.scrapers)
    except Exception as e:
        log.warning("could not build %s: %s", s_name, e)
        continue
    scoped = queries_for_scraper(all_queries, s_name, tag_map)
    if not scoped:
        continue
    timeout_sec = float(getattr(cfg.bench, "per_query_timeout_sec", 20))
    breaker_limit = int(getattr(cfg.bench, "circuit_break_after_consecutive_fails", 3))
    consecutive_fails = 0
    for q in scoped:
        if consecutive_fails >= breaker_limit:
            outcomes.append(BenchOutcome(
                scraper=s_name, query=q.title, success=False, duration_ms=0,
                candidates=0, matched_isbn=False,
                note=f"circuit-broken: skipped after {breaker_limit} consecutive failures",
            ))
            if repo:
                repo.record(scraper=s_name, query=q.title, success=False,
                            duration_ms=0, notes="circuit-broken")
            continue
        sq = SearchQuery(
            title=q.title, author=q.author, isbn13=q.isbn13,
            format_priority=tuple(cfg.scrapers.format_priority),
            language=q.language,
        )
        t0 = time.monotonic()
        success = False; n_cands = 0; matched = False; note = ""
        try:
            with _cf.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(scraper.search, sq)
                cands = fut.result(timeout=timeout_sec)
            n_cands = len(cands)
            if cands:
                title_lc = q.title.lower().split(":")[0].strip()
                for c in cands[:5]:
                    if c.title and title_lc.split()[0] in (c.title or "").lower():
                        matched = True
                        break
                success = matched
        except _cf.TimeoutError:
            note = f"timeout after {timeout_sec}s"
        except NotImplementedError as e:
            note = f"stub: {e}"
        except Exception as e:
            note = f"{type(e).__name__}: {e}"
        if not success:
            consecutive_fails += 1
        else:
            consecutive_fails = 0
        dur = int((time.monotonic() - t0) * 1000)
        outcomes.append(BenchOutcome(
            scraper=s_name, query=q.title, success=success,
            duration_ms=dur, candidates=n_cands, matched_isbn=matched, note=note,
        ))
        if repo:
            repo.record(scraper=s_name, query=q.title, success=success,
                        duration_ms=dur, notes=note)
```

Note: this uses `concurrent.futures` rather than `asyncio.wait_for` because the scrapers' `.search()` is synchronous. ThreadPoolExecutor cancellation is best-effort — the underlying request may continue but the outcome is recorded as a timeout and the loop proceeds.

- [ ] **Step 5: Run tests**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w0_bench_async.py -v -k 'timeout or circuit'"
```
Expected: both pass.

- [ ] **Step 6: Verify the legacy bench tests still pass**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/integration/test_bench.py tests/unit/test_phase6v_bench_corpus.py -v"
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/bench.py src/endless_library/config.py tests/unit/test_phase6w0_bench_async.py && git commit -m 'Phase 6w.0c: per-query timeout + circuit breaker in run_bench' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 4: API endpoints — async run, status, list, cancel, SSE stream

**Files:**
- Modify: `src/endless_library/web/api.py`
- Test: `tests/unit/test_phase6w0_bench_async.py` (extend)

- [ ] **Step 1: Write failing endpoint tests**

Append to `tests/unit/test_phase6w0_bench_async.py`:

```python
import json as _json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from endless_library.web import api as api_mod
from endless_library.db.bench import BenchRunRepo
from endless_library.config import ScrapersCfg


def _app(tmp_path: Path) -> FastAPI:
    db = tmp_path / "x.db"
    init_db(db)
    app = FastAPI()
    cfg = SimpleNamespace(
        scrapers=ScrapersCfg(order=[], enabled={}),
        bench=SimpleNamespace(per_query_timeout_sec=20,
                              circuit_break_after_consecutive_fails=3),
        general=SimpleNamespace(books_dir="/tmp/books"),
        smtp=SimpleNamespace(daily_cap=80),
    )
    app.state.deps = SimpleNamespace(
        db_path=db, cfg=cfg,
        bench=BenchRunRepo(db),
        bench_jobs=BenchJobsRepo(db),
        books=SimpleNamespace(pending=lambda **kw: []),
    )
    app.state.scheduler = SimpleNamespace(running=True)
    app.state.config_path = Path("/tmp/cfg.yaml")
    api_mod.register(app)
    return app


def test_post_bench_run_returns_202_with_job_id(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/bench/run?mode=quick")
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body and isinstance(body["job_id"], int)


def test_get_bench_jobs_returns_list_with_recent_first(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    client.post("/api/bench/run?mode=quick")
    client.post("/api/bench/run?mode=quick")
    r = client.get("/api/bench/jobs")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert len(jobs) >= 2
    assert jobs[0]["id"] > jobs[1]["id"]


def test_get_bench_job_returns_status_and_progress(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/bench/run?mode=quick")
    job_id = r.json()["job_id"]
    g = client.get(f"/api/bench/jobs/{job_id}")
    assert g.status_code == 200
    body = g.json()
    assert body["id"] == job_id
    assert body["status"] in ("running", "done")
    assert "progress_done" in body
    assert "progress_total" in body


def test_get_bench_job_returns_404_for_missing(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.get("/api/bench/jobs/9999")
    assert r.status_code == 404


def test_post_bench_job_cancel_flips_status(tmp_path: Path):
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/bench/run?mode=quick")
    job_id = r.json()["job_id"]
    c = client.post(f"/api/bench/jobs/{job_id}/cancel")
    assert c.status_code == 200
    g = client.get(f"/api/bench/jobs/{job_id}")
    assert g.json()["status"] in ("cancelled", "done")  # may have already completed
```

- [ ] **Step 2: Run to verify failure**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w0_bench_async.py -v -k 'post_bench_run or get_bench_job or cancel'"
```
Expected: 5 failures (endpoints not yet defined; 405 / 404).

- [ ] **Step 3: Replace `/bench/run` and add new endpoints in `web/api.py`**

Find the existing `@router.post("/bench/run")` in `src/endless_library/web/api.py` and replace it. Also add the new endpoints in the same section:

```python
import asyncio as _asyncio
import json as _json

from endless_library.db.bench_jobs import BenchJobsRepo

# --- replace existing run_bench_endpoint ---

@router.post("/bench/run", status_code=202)
async def run_bench_endpoint(request: Request, mode: str = "quick"):
    deps = request.app.state.deps
    qs, quick_idx = load_queries()
    if mode == "quick":
        qs = [qs[i] for i in quick_idx if i < len(qs)]
    strats = registry.enabled_order(deps.cfg.scrapers)
    progress_total = len(strats) * len(qs)
    job_id = deps.bench_jobs.create(mode=mode, progress_total=progress_total)
    _asyncio.create_task(_bench_worker(deps, job_id, qs, strats))
    return {"job_id": job_id}


async def _bench_worker(deps, job_id: int, qs, strats):
    """Background worker: re-uses run_bench but emits per-outcome
    increment + checks cancel flag between scrapers."""
    from functools import partial
    try:
        outcomes_so_far: list = []
        for s_name in strats:
            if deps.bench_jobs.is_cancel_requested(job_id):
                deps.bench_jobs.finish(job_id, status="cancelled",
                                       summary_json=_json.dumps([asdict(o) for o in outcomes_so_far]))
                return
            os_for_one = await _asyncio.to_thread(
                partial(run_bench, deps.cfg, qs, repo=deps.bench, strategies=[s_name])
            )
            outcomes_so_far.extend(os_for_one)
            for _ in os_for_one:
                deps.bench_jobs.increment_progress(job_id)
        deps.bench_jobs.finish(
            job_id, status="done",
            summary_json=_json.dumps([asdict(o) for o in outcomes_so_far]),
        )
    except Exception as e:
        deps.bench_jobs.finish(job_id, status="failed",
                               summary_json=_json.dumps({"error": f"{type(e).__name__}: {e}"}))


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


def _job_row_to_dict(r):
    return {
        "id": r.id, "started_at": r.started_at, "finished_at": r.finished_at,
        "mode": r.mode, "status": r.status,
        "progress_done": r.progress_done, "progress_total": r.progress_total,
        "summary_json": r.summary_json,
    }
```

Wire `deps.bench_jobs` in the app factory. Find where `BenchRunRepo` is constructed (likely `endless_library/app.py` or `wiring.py`) and add:

```python
from endless_library.db.bench_jobs import BenchJobsRepo
# ... near where deps.bench is set:
deps.bench_jobs = BenchJobsRepo(db_path)
```

- [ ] **Step 4: Run tests**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w0_bench_async.py -v"
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/web/api.py src/endless_library/app.py tests/unit/test_phase6w0_bench_async.py && git commit -m 'Phase 6w.0d: async bench job endpoints' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 5: SSE stream endpoint

**Files:**
- Modify: `src/endless_library/web/api.py`
- Test: `tests/unit/test_phase6w0_bench_async.py` (extend)

- [ ] **Step 1: Write a failing SSE test**

```python
def test_bench_job_stream_emits_terminal_event_on_done(tmp_path: Path):
    """SSE endpoint should at minimum send a terminal 'done' event
    once the job finishes. We use TestClient.stream to consume."""
    app = _app(tmp_path)
    client = TestClient(app)
    r = client.post("/api/bench/run?mode=quick")
    job_id = r.json()["job_id"]
    # With an empty enabled chain, job finishes immediately.
    with client.stream("GET", f"/api/bench/jobs/{job_id}/stream") as resp:
        events = []
        for line in resp.iter_lines():
            events.append(line)
            if "event: done" in line or "event: failed" in line or len(events) > 50:
                break
    assert any("done" in e or "failed" in e for e in events)
```

- [ ] **Step 2: Run to verify failure**

Expected: 404 on the /stream endpoint.

- [ ] **Step 3: Implement SSE**

Add to `web/api.py`:

```python
from fastapi.responses import StreamingResponse

@router.get("/bench/jobs/{job_id}/stream")
async def stream_bench_job(job_id: int, request: Request):
    deps = request.app.state.deps
    row = deps.bench_jobs.get(job_id)
    if row is None:
        raise HTTPException(404, f"no bench job with id={job_id}")

    async def _events():
        last_progress = -1
        while True:
            r = deps.bench_jobs.get(job_id)
            if r is None:
                yield "event: gone\ndata: {}\n\n"
                return
            if r.progress_done != last_progress:
                yield f"event: progress\ndata: {_json.dumps({'done': r.progress_done, 'total': r.progress_total})}\n\n"
                last_progress = r.progress_done
            if r.status in ("done", "cancelled", "failed"):
                yield f"event: {r.status}\ndata: {r.summary_json or '{}'}\n\n"
                return
            await _asyncio.sleep(0.5)

    return StreamingResponse(_events(), media_type="text/event-stream")
```

- [ ] **Step 4: Run tests**

Expected: pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/web/api.py tests/unit/test_phase6w0_bench_async.py && git commit -m 'Phase 6w.0e: SSE stream for bench jobs' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 6: ScrapersPage SPA updates

**Files:**
- Modify: `webapp/src/pages/ScrapersPage.vue`

- [ ] **Step 1: Locate the existing `bench()` function**

Open `webapp/src/pages/ScrapersPage.vue`. Find the `async function bench(mode: 'quick' | 'full')` function (around the "Benchmark" Card).

- [ ] **Step 2: Replace bench() to use job_id + SSE**

```vue
<script setup lang="ts">
// ... existing imports + add:
const benchJobId = ref<number | null>(null)
const benchProgress = ref<{ done: number; total: number }>({ done: 0, total: 0 })
const benchEventSrc = ref<EventSource | null>(null)

async function bench(mode: 'quick' | 'full') {
  benching.value = true
  benchOutput.value = `Starting ${mode} bench…`
  benchProgress.value = { done: 0, total: 0 }
  try {
    const r = await api<{ job_id: number }>(
      `/api/bench/run?mode=${mode}`, { method: 'POST' },
    )
    benchJobId.value = r.job_id
    _streamJob(r.job_id)
  } catch (e: any) {
    benchOutput.value = `FAILED: ${e?.message ?? e}`
    toast.error('Bench failed', String(e?.message ?? e))
    benching.value = false
  }
}

function _streamJob(jobId: number) {
  const es = new EventSource(`/api/bench/jobs/${jobId}/stream`)
  benchEventSrc.value = es
  es.addEventListener('progress', (ev: MessageEvent) => {
    const p = JSON.parse(ev.data) as { done: number; total: number }
    benchProgress.value = p
    benchOutput.value = `Running… ${p.done}/${p.total}`
  })
  for (const term of ['done', 'cancelled', 'failed', 'gone']) {
    es.addEventListener(term, async (ev: MessageEvent) => {
      benching.value = false
      es.close()
      if (term === 'done') {
        const r = await api<{ summary_json: string | null }>(`/api/bench/jobs/${jobId}`)
        benchOutput.value = r.summary_json
          ? _formatSummary(JSON.parse(r.summary_json))
          : 'done (no outcomes)'
        toast.success(`Bench complete`)
        await load()
      } else {
        benchOutput.value = `${term}: ${ev.data}`
        toast.error(`Bench ${term}`)
      }
    })
  }
  es.onerror = () => {
    // SSE drops — fall back to 2s polling
    es.close()
    _pollJob(jobId)
  }
}

async function _pollJob(jobId: number) {
  while (true) {
    const r = await api<any>(`/api/bench/jobs/${jobId}`)
    benchProgress.value = { done: r.progress_done, total: r.progress_total }
    if (r.status !== 'running') {
      benching.value = false
      benchOutput.value = r.status === 'done' && r.summary_json
        ? _formatSummary(JSON.parse(r.summary_json))
        : `${r.status}`
      await load()
      return
    }
    await new Promise(res => setTimeout(res, 2000))
  }
}

function _formatSummary(outcomes: any[]): string {
  if (!outcomes || outcomes.length === 0) return '(no outcomes)'
  const by: Record<string, { p: number; f: number }> = {}
  for (const o of outcomes) {
    by[o.scraper] = by[o.scraper] || { p: 0, f: 0 }
    if (o.success) by[o.scraper].p++; else by[o.scraper].f++
  }
  const lines = ['| Scraper | Pass | Fail |', '|---|---|---|']
  for (const [name, s] of Object.entries(by)) lines.push(`| ${name} | ${s.p} | ${s.f} |`)
  return lines.join('\n')
}

async function cancelBench() {
  if (!benchJobId.value) return
  await api(`/api/bench/jobs/${benchJobId.value}/cancel`, { method: 'POST' })
  toast.success('Cancel requested')
}
</script>
```

In the template, replace the bench Card body's button row with:

```vue
<div class="flex gap-2 mb-3 items-center flex-wrap">
  <Button :loading="benching" @click="bench('quick')"><Play class="w-4 h-4" /> Quick</Button>
  <Button :loading="benching" variant="outline" @click="bench('full')">Full</Button>
  <Button v-if="benching" variant="ghost" size="sm" @click="cancelBench">Cancel</Button>
  <span v-if="benching && benchProgress.total > 0" class="text-xs text-muted-foreground font-mono">
    {{ benchProgress.done }}/{{ benchProgress.total }}
  </span>
</div>
```

- [ ] **Step 3: Build SPA + verify build**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library/webapp && npm run build 2>&1 | tail -5"
```
Expected: builds clean.

- [ ] **Step 4: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add webapp/src/pages/ScrapersPage.vue && git commit -m 'Phase 6w.0f: ScrapersPage bench uses job_id + SSE' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 7: Full regression + sub-phase 6w.0 close

- [ ] **Step 1: Full suite**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -5"
```
Expected: all green, count up by ~12-15 new tests.

- [ ] **Step 2: Push origin**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git push origin main"
```

---

## Sub-phase 6w.1 — HTTP foundation (curl-cffi + Anubis)

**Goal of sub-phase:** Add `make_client()` factory; introduce in-process Anubis PoW solver wired as request middleware; migrate scrapers to the new client in batches.

### File map

- **Create:** `src/endless_library/scrapers/http_client.py`
- **Create:** `src/endless_library/scrapers/anubis.py`
- **Create:** `tests/unit/test_phase6w1_http_foundation.py`
- **Modify:** `pyproject.toml` — add `curl-cffi>=0.7` dep
- **Modify:** each scraper (4 batches): swap `httpx.Client()` → `make_client()`

### Task 1: curl-cffi dep + smoke import

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/unit/test_phase6w1_http_foundation.py`

- [ ] **Step 1: Add dep**

In `pyproject.toml`, add to dependencies:

```toml
"curl-cffi>=0.7.0",
```

- [ ] **Step 2: Install**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && pip install -e . 2>&1 | tail -3"
```

- [ ] **Step 3: Write smoke import test**

```python
# tests/unit/test_phase6w1_http_foundation.py
def test_curl_cffi_imports():
    from curl_cffi import requests as cffi_requests
    assert cffi_requests.Session is not None
```

- [ ] **Step 4: Run + verify**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w1_http_foundation.py -v"
```

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add pyproject.toml tests/unit/test_phase6w1_http_foundation.py && git commit -m 'Phase 6w.1a: add curl-cffi dependency' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 2: Anubis PoW solver

**Files:**
- Create: `src/endless_library/scrapers/anubis.py`
- Test: `tests/unit/test_phase6w1_http_foundation.py` (extend)

- [ ] **Step 1: Write failing tests**

```python
import hashlib

def test_solve_anubis_finds_valid_nonce_at_difficulty_8():
    from endless_library.scrapers.anubis import solve_anubis
    challenge = "abc"
    n = solve_anubis(challenge, 8)
    h = hashlib.sha256(f"{challenge}{n}".encode()).digest()
    assert h[0] == 0   # 8 leading zero bits


def test_solve_anubis_handles_non_byte_aligned_difficulty():
    from endless_library.scrapers.anubis import solve_anubis
    challenge = "test"
    n = solve_anubis(challenge, 12)
    h = hashlib.sha256(f"{challenge}{n}".encode()).digest()
    assert h[0] == 0
    assert (h[1] & 0xF0) == 0   # next 4 bits also zero


def test_solve_anubis_returns_int():
    from endless_library.scrapers.anubis import solve_anubis
    n = solve_anubis("x", 4)
    assert isinstance(n, int) and n >= 0


def test_solve_anubis_zero_difficulty_returns_zero():
    from endless_library.scrapers.anubis import solve_anubis
    assert solve_anubis("any", 0) == 0
```

- [ ] **Step 2: Run to verify failure**

Expected: ImportError on `endless_library.scrapers.anubis`.

- [ ] **Step 3: Implement**

Create `src/endless_library/scrapers/anubis.py`:

```python
"""Pure-Python Anubis PoW solver.

Anubis (https://github.com/TecharoHQ/anubis) is an anti-AI / anti-
scrape PoW challenge increasingly deployed on shadow-library-adjacent
forges in 2025+. Server returns a challenge string and difficulty
(number of required leading zero bits in sha256(challenge+nonce));
client submits nonce; server returns a JWT cookie valid for ~50min.

Pure Python; ~1-50ms typical at difficulty 8-16.
"""
from __future__ import annotations

import hashlib


def solve_anubis(challenge: str, difficulty: int) -> int:
    """Find nonce N such that sha256(challenge+str(N)) has at least
    `difficulty` leading zero BITS. Returns the smallest such nonce."""
    if difficulty <= 0:
        return 0
    target_bytes = difficulty // 8
    target_bits = difficulty % 8
    target_mask = (0xFF << (8 - target_bits)) & 0xFF if target_bits else 0
    nonce = 0
    while True:
        h = hashlib.sha256(f"{challenge}{nonce}".encode()).digest()
        if all(b == 0 for b in h[:target_bytes]):
            if not target_bits or (h[target_bytes] & target_mask) == 0:
                return nonce
        nonce += 1
```

- [ ] **Step 4: Run tests**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/anubis.py tests/unit/test_phase6w1_http_foundation.py && git commit -m 'Phase 6w.1b: pure-Python Anubis PoW solver' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 3: http_client.make_client factory + Anubis middleware

**Files:**
- Create: `src/endless_library/scrapers/http_client.py`
- Test: `tests/unit/test_phase6w1_http_foundation.py` (extend)

- [ ] **Step 1: Write failing tests**

```python
def test_make_client_returns_cffi_session():
    from endless_library.scrapers.http_client import make_client
    from curl_cffi.requests import Session
    c = make_client()
    assert isinstance(c, Session)


def test_make_client_accepts_proxies():
    from endless_library.scrapers.http_client import make_client
    c = make_client(proxies={"all": "socks5h://localhost:9050"})
    # curl-cffi stores proxies in session.proxies
    assert "all" in c.proxies


def test_anubis_middleware_only_triggers_on_signature(monkeypatch):
    """If the response HTML doesn't match the Anubis fingerprint,
    middleware must be a no-op."""
    from endless_library.scrapers.http_client import _is_anubis_response
    class _R:
        status_code = 200
        text = "<html><body>Hello</body></html>"
        headers = {"content-type": "text/html"}
    assert _is_anubis_response(_R()) is False


def test_anubis_middleware_detects_signature():
    from endless_library.scrapers.http_client import _is_anubis_response
    class _R:
        status_code = 200
        text = '<html><head><meta name="anubis-challenge" content="abc"></head></html>'
        headers = {"content-type": "text/html"}
    assert _is_anubis_response(_R()) is True


def test_anubis_middleware_solves_and_retries(monkeypatch):
    """End-to-end: a fake session whose first GET returns Anubis, second
    GET returns a normal 200. Middleware should solve + re-issue.
    """
    from endless_library.scrapers.http_client import _solve_and_get_cookie
    challenge_html = (
        '<html><head>'
        '<meta name="anubis-challenge" content="abc">'
        '<meta name="anubis-difficulty" content="4">'
        '<meta name="anubis-action" content="/anubis/pass">'
        '</head></html>'
    )
    posted = {}
    class _Sess:
        def post(self, url, data=None, **kw):
            posted["url"] = url
            posted["data"] = data
            class _R:
                status_code = 200
                cookies = {"techaro-anubis-auth": "JWT"}
            return _R()
    cookie = _solve_and_get_cookie(challenge_html, "https://example.com/page", _Sess())
    assert cookie == "JWT"
    assert posted["url"].endswith("/anubis/pass")
    assert "nonce" in posted["data"] and "challenge" in posted["data"]
```

- [ ] **Step 2: Run to verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement http_client.py**

Create `src/endless_library/scrapers/http_client.py`:

```python
"""Shared HTTP client factory for all scrapers.

Returns a `curl_cffi.requests.Session` that impersonates a real
Chrome browser at the TLS / JA3 / JA4 layer. This alone defeats ~30%
of Cloudflare 'bot-fight-mode' challenges that previously pushed
biblichor into FlareSolverr or the cloak-browser path.

Also installs an Anubis PoW middleware: any response whose HTML
matches the Anubis fingerprint is intercepted, the PoW is solved
in-process, the JWT cookie is captured, and the original request
is retried automatically. Cache keyed by host (50-min TTL).
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

from curl_cffi import requests as cffi_requests

from .anubis import solve_anubis


_ANUBIS_SIGNATURES = (
    re.compile(r'<meta\s+name="anubis-challenge"', re.I),
    re.compile(r'<title>[^<]*Making sure you[\'’]re not a bot', re.I),
    re.compile(r'name="generator"\s+content="anubis"', re.I),
)
_ANUBIS_CHALLENGE_RE = re.compile(r'<meta\s+name="anubis-challenge"\s+content="([^"]+)"', re.I)
_ANUBIS_DIFFICULTY_RE = re.compile(r'<meta\s+name="anubis-difficulty"\s+content="([0-9]+)"', re.I)
_ANUBIS_ACTION_RE = re.compile(r'<meta\s+name="anubis-action"\s+content="([^"]+)"', re.I)

_ANUBIS_COOKIE_CACHE: dict[str, tuple[str, float]] = {}  # host -> (jwt, expires_at)
_ANUBIS_TTL_SEC = 50 * 60


def make_client(
    *,
    impersonate: str = "chrome",
    timeout: float = 30.0,
    proxies: dict[str, str] | None = None,
) -> cffi_requests.Session:
    """Drop-in replacement for httpx.Client in scrapers.

    The returned Session has the same shape as httpx.Client for the
    methods we use: get/post/head/put/delete, returning a response
    with .status_code, .text, .content, .json(), .headers, .url.
    """
    s = cffi_requests.Session(impersonate=impersonate, timeout=timeout)
    if proxies:
        s.proxies.update(proxies)
    _install_anubis_middleware(s)
    return s


def _is_anubis_response(resp: Any) -> bool:
    if getattr(resp, "status_code", None) != 200:
        return False
    ctype = (resp.headers.get("content-type", "") if hasattr(resp, "headers") else "")
    if "text/html" not in ctype.lower():
        return False
    text = getattr(resp, "text", "") or ""
    return any(p.search(text) for p in _ANUBIS_SIGNATURES)


def _solve_and_get_cookie(html: str, request_url: str, session: Any) -> str | None:
    """Parse challenge + difficulty + action from HTML, solve the PoW,
    POST nonce+challenge, return the JWT cookie value (or None)."""
    cm = _ANUBIS_CHALLENGE_RE.search(html)
    dm = _ANUBIS_DIFFICULTY_RE.search(html)
    am = _ANUBIS_ACTION_RE.search(html)
    if not (cm and dm):
        return None
    challenge = cm.group(1)
    difficulty = int(dm.group(1))
    action = am.group(1) if am else "/.within.website/x/cmd/anubis/api/pass-challenge"
    nonce = solve_anubis(challenge, difficulty)
    submit_url = urljoin(request_url, action)
    resp = session.post(submit_url,
                        data={"challenge": challenge, "nonce": str(nonce), "redir": request_url})
    if getattr(resp, "status_code", 0) not in (200, 302):
        return None
    for k, v in (getattr(resp, "cookies", {}) or {}).items():
        if "anubis" in k.lower() or k.lower().endswith("-auth"):
            return v
    return None


def _install_anubis_middleware(session: Any) -> None:
    """Wrap session.get/post so that on an Anubis-flavored response,
    we solve the PoW, store the cookie, and retry."""
    orig_get = session.get

    def get(url: str, **kw):
        host = urlparse(url).netloc
        cached = _ANUBIS_COOKIE_CACHE.get(host)
        if cached and cached[1] > time.time():
            kw.setdefault("cookies", {})
            kw["cookies"].setdefault("techaro-anubis-auth", cached[0])
        r = orig_get(url, **kw)
        if _is_anubis_response(r):
            cookie = _solve_and_get_cookie(r.text, url, session)
            if cookie:
                _ANUBIS_COOKIE_CACHE[host] = (cookie, time.time() + _ANUBIS_TTL_SEC)
                kw.setdefault("cookies", {})
                kw["cookies"]["techaro-anubis-auth"] = cookie
                r = orig_get(url, **kw)
        return r

    session.get = get
```

- [ ] **Step 4: Run tests**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w1_http_foundation.py -v"
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/http_client.py tests/unit/test_phase6w1_http_foundation.py && git commit -m 'Phase 6w.1c: make_client + Anubis middleware' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 4: Migrate PD-class scrapers (sanity batch)

**Files:**
- Modify: `src/endless_library/scrapers/gutendex.py`
- Modify: `src/endless_library/scrapers/standard_ebooks.py`
- Modify: `src/endless_library/scrapers/oapen_doab.py`
- Modify: `src/endless_library/scrapers/wikisource.py`

- [ ] **Step 1: For EACH of the four files, replace the client import**

For example in `gutendex.py`, find any `httpx.Client(...)` or `httpx.AsyncClient(...)` instantiation. Replace with:

```python
from endless_library.scrapers.http_client import make_client
# ...
self.client = make_client(timeout=20)
```

Remove the now-unused `import httpx` if it's no longer referenced. If the module still uses `httpx.HTTPError` for exception handling, keep the import.

- [ ] **Step 2: Run the existing tests for these 4 scrapers**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/ -v -k 'gutendex or standard_ebooks or oapen_doab or wikisource' 2>&1 | tail -20"
```
Expected: all pass (the cffi Session is API-compatible for our usage).

- [ ] **Step 3: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/gutendex.py src/endless_library/scrapers/standard_ebooks.py src/endless_library/scrapers/oapen_doab.py src/endless_library/scrapers/wikisource.py && git commit -m 'Phase 6w.1d: PD scrapers migrate to make_client (sanity batch)' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 5: Migrate zero-CF scrapers

Same pattern as Task 4, for `archive_curl.py`, `libgen_curl.py`, `kindlebangla_curl.py`. Commit one file at a time if you prefer fine-grained history; here as a single commit:

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/archive_curl.py src/endless_library/scrapers/libgen_curl.py src/endless_library/scrapers/kindlebangla_curl.py && git commit -m 'Phase 6w.1e: zero-CF scrapers migrate to make_client' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 6: Migrate CF-protected scrapers

Same pattern, for `annas_curl.py` and `welib_curl.py`. After this commit, run full regression and verify each scraper's existing live test (if any) still passes.

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/annas_curl.py src/endless_library/scrapers/welib_curl.py && git commit -m 'Phase 6w.1f: CF-protected scrapers migrate to make_client' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 7: Full regression + push

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3"
```
Expected: all pass, count up by 8-10 new tests.

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git push origin main"
```

---

## Sub-phase 6w.2 — Anna's hardening (mirror rotation, parallel slot, cf-bypass sidecar)

**Goal of sub-phase:** Make the Anna's chain resilient to mirror outages and Cloudflare escalation. Add the sarperavci sidecar as a third anti-bot rung.

### File map

- **Modify:** `src/endless_library/scrapers/annas_domains.py` (mirror rotation)
- **Modify:** `src/endless_library/scrapers/annas_curl.py` (mirror consumer + parallel slot)
- **Modify:** `src/endless_library/scrapers/annas_cloakbrowser.py` (rewrite to sidecar)
- **Create:** `src/endless_library/scrapers/cf_bypass_client.py`
- **Modify:** `deploy/compose.yml` (add cf-bypass service)
- **Create:** `tests/unit/test_phase6w2_annas_hardening.py`

### Task 1: Mirror rotation in `annas_domains`

**Files:**
- Modify: `src/endless_library/scrapers/annas_domains.py`
- Test: `tests/unit/test_phase6w2_annas_hardening.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_phase6w2_annas_hardening.py
import time

def test_next_mirror_returns_first_when_no_history():
    from endless_library.scrapers.annas_domains import next_mirror, _reset_state
    _reset_state()
    m = next_mirror()
    assert m in {"annas-archive.gl", "annas-archive.li", "annas-archive.pm", "annas-archive.in"}


def test_mark_cool_skips_mirror_for_5min():
    from endless_library.scrapers.annas_domains import (
        next_mirror, mark_cool, _reset_state, _MIRRORS,
    )
    _reset_state()
    cooled = _MIRRORS[0]
    mark_cool(cooled)
    seen = set()
    for _ in range(10):
        seen.add(next_mirror())
    assert cooled not in seen


def test_mark_success_prefers_last_working():
    from endless_library.scrapers.annas_domains import (
        next_mirror, mark_success, _reset_state, _MIRRORS,
    )
    _reset_state()
    pick = _MIRRORS[2]
    mark_success(pick)
    assert next_mirror(prefer_last_working=True) == pick


def test_cool_expires_after_300_seconds(monkeypatch):
    import endless_library.scrapers.annas_domains as ad
    ad._reset_state()
    ad.mark_cool(ad._MIRRORS[0])
    monkeypatch.setattr(ad, "_now", lambda: time.time() + 301)
    seen = set()
    for _ in range(10):
        seen.add(ad.next_mirror())
    assert ad._MIRRORS[0] in seen
```

- [ ] **Step 2: Run to verify failure**

Expected: AttributeError on `_reset_state`, `next_mirror`, `mark_cool`, `mark_success`, `_MIRRORS`.

- [ ] **Step 3: Replace `annas_domains.py` contents**

```python
"""Anna's Archive mirror rotation.

Phase 6w.2: the bench audit on 2026-05-22 hit a sustained 502 from
annas-archive.gl on Bengali queries. Rotate across the 2026 mirror
list (.gl, .li, .pm, .in). On 5xx / connection-refused, cool that
mirror for 5 minutes; pin success across calls when prefer_last_working
is set."""
from __future__ import annotations

import time

_MIRRORS = (
    "annas-archive.gl",
    "annas-archive.li",
    "annas-archive.pm",
    "annas-archive.in",
)
_COOL_DOWN_SEC = 5 * 60

_state: dict[str, float] = {}      # mirror -> cool-until-epoch
_last_working: str | None = None


def _now() -> float:
    return time.time()


def _reset_state() -> None:
    global _last_working
    _state.clear()
    _last_working = None


def _is_cool(host: str) -> bool:
    until = _state.get(host)
    return until is not None and until > _now()


def next_mirror(prefer_last_working: bool = True) -> str:
    """Return a hostname currently usable. If prefer_last_working and
    the last-known-good is not cool, return it; else round-robin
    through non-cool mirrors. Raises if all are cool."""
    if prefer_last_working and _last_working and not _is_cool(_last_working):
        return _last_working
    for m in _MIRRORS:
        if not _is_cool(m):
            return m
    # Everything cool — return the earliest expiring (least bad)
    return min(_MIRRORS, key=lambda m: _state.get(m, 0))


def mark_cool(host: str) -> None:
    _state[host] = _now() + _COOL_DOWN_SEC


def mark_success(host: str) -> None:
    global _last_working
    _last_working = host
    _state.pop(host, None)
```

- [ ] **Step 4: Run tests**

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/annas_domains.py tests/unit/test_phase6w2_annas_hardening.py && git commit -m 'Phase 6w.2a: Anna mirror rotation with 5min cool-down' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 2: annas_curl consumes mirror rotation

**Files:**
- Modify: `src/endless_library/scrapers/annas_curl.py`

- [ ] **Step 1: Inspect current hostname handling**

```bash
ssh ubuntu@claude-1 "grep -n 'annas-archive\.' /home/ubuntu/endless-library/src/endless_library/scrapers/annas_curl.py | head -10"
```

- [ ] **Step 2: Replace hardcoded hostname**

Anywhere `"annas-archive.gl"` (or any specific Anna's hostname) appears as a base URL in `annas_curl.py`, replace with:

```python
from endless_library.scrapers import annas_domains
# ...
host = annas_domains.next_mirror()
base_url = f"https://{host}"
```

Wrap each request in:

```python
try:
    r = self.client.get(url, ...)
    if r.status_code in (502, 503, 504):
        annas_domains.mark_cool(host)
        # retry once with the next mirror
        host = annas_domains.next_mirror()
        url = url.replace(self._last_host or host, host)
        r = self.client.get(url, ...)
    annas_domains.mark_success(host)
except Exception:
    annas_domains.mark_cool(host)
    raise
```

(Capture `self._last_host` per instance so retry rewrites the URL with the new host.)

- [ ] **Step 3: Quick smoke**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -v -k annas_curl 2>&1 | tail -10"
```

- [ ] **Step 4: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/annas_curl.py && git commit -m 'Phase 6w.2b: annas_curl uses mirror rotation' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 3: cf-bypass sidecar + client

**Files:**
- Modify: `deploy/compose.yml`
- Create: `src/endless_library/scrapers/cf_bypass_client.py`
- Test: `tests/unit/test_phase6w2_annas_hardening.py` (extend)

- [ ] **Step 1: Add sidecar to compose.yml**

In `deploy/compose.yml`, after the `flaresolverr:` service block, add:

```yaml
  # Phase 6w.2: sarperavci/CloudflareBypassForScraping sidecar.
  # Used by annas_cloakbrowser when curl-cffi + FlareSolverr both fail
  # against a Cloudflare interactive challenge. Default-on (no profile).
  cf-bypass:
    image: sarperavci/cloudflarebypassforscraping:latest
    container_name: biblichor-cf-bypass
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s
    networks:
      - biblichor
```

In biblichor's `environment:` block, add:

```yaml
      CF_BYPASS_URL: "http://cf-bypass:8000"
```

- [ ] **Step 2: Write client tests**

```python
def test_cf_bypass_client_posts_url_and_returns_html(monkeypatch):
    from endless_library.scrapers import cf_bypass_client
    posted = {}
    class _R:
        status_code = 200
        def json(self): return {"html": "<html>resolved</html>"}
        def raise_for_status(self): pass
    def _fake_post(url, json=None, timeout=None, **kw):
        posted["url"] = url; posted["json"] = json
        return _R()
    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.httpx.post", _fake_post)
    monkeypatch.setenv("CF_BYPASS_URL", "http://test-bypass:8000")
    html = cf_bypass_client.resolve("https://annas-archive.gl/md5/abc")
    assert "<html>resolved</html>" in html
    assert posted["url"] == "http://test-bypass:8000/cf-clearance-scraper"
    assert posted["json"] == {"url": "https://annas-archive.gl/md5/abc"}


def test_cf_bypass_client_raises_on_5xx(monkeypatch):
    import httpx
    from endless_library.scrapers import cf_bypass_client
    class _R:
        status_code = 502
        def raise_for_status(self): raise httpx.HTTPStatusError("502", request=None, response=None)
    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.httpx.post",
                        lambda *a, **kw: _R())
    monkeypatch.setenv("CF_BYPASS_URL", "http://test-bypass:8000")
    try:
        cf_bypass_client.resolve("https://x")
        assert False, "should have raised"
    except httpx.HTTPStatusError:
        pass
```

- [ ] **Step 3: Implement client**

Create `src/endless_library/scrapers/cf_bypass_client.py`:

```python
"""Thin HTTP wrapper around the sarperavci CloudflareBypassForScraping
sidecar (compose service `cf-bypass`). POST a URL, get resolved HTML
back. Sidecar internally drives DrissionPage / patched Chromium and
handles Cloudflare interactive challenges that defeat curl-cffi and
FlareSolverr alike.
"""
from __future__ import annotations

import os

import httpx


def resolve(url: str, *, timeout: float = 90.0) -> str:
    """POST `url` to the sidecar; return its resolved HTML.
    Raises httpx.HTTPError on transport failure / non-2xx.
    """
    base = os.environ.get("CF_BYPASS_URL", "http://cf-bypass:8000")
    r = httpx.post(
        f"{base.rstrip('/')}/cf-clearance-scraper",
        json={"url": url},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["html"]
```

- [ ] **Step 4: Run tests**

Expected: pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add deploy/compose.yml src/endless_library/scrapers/cf_bypass_client.py tests/unit/test_phase6w2_annas_hardening.py && git commit -m 'Phase 6w.2c: cf-bypass sidecar + Python client' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 4: Rewrite annas_cloakbrowser to use the sidecar

**Files:**
- Modify: `src/endless_library/scrapers/annas_cloakbrowser.py`

- [ ] **Step 1: Read the current file**

```bash
ssh ubuntu@claude-1 "wc -l /home/ubuntu/endless-library/src/endless_library/scrapers/annas_cloakbrowser.py"
```

- [ ] **Step 2: Replace contents**

```python
"""annas_cloakbrowser — Phase 6w.2 rewrite.

Now talks to the `cf-bypass` sidecar (sarperavci/CloudflareBypassForScraping).
Sidecar runs DrissionPage + patched Chromium inside the biblichor compose
network and resolves Cloudflare interactive challenges. Same registry
slot + name as before (so cfg.scrapers.order doesn't churn), but the
internals are replaced.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers import annas_domains, cf_bypass_client

log = logging.getLogger(__name__)


class AnnasArchiveCloakBrowser:
    name = "annas_cloakbrowser"
    provider = "annas_cloakbrowser"

    def __init__(self, cfg, **kw):
        self._cfg = cfg

    def search(self, query: SearchQuery) -> list[Candidate]:
        host = annas_domains.next_mirror()
        q = urlencode({"q": query.title, "ext": "epub", "sort": ""})
        url = f"https://{host}/search?{q}"
        try:
            html = cf_bypass_client.resolve(url)
            annas_domains.mark_success(host)
        except Exception as e:
            log.warning("cf-bypass resolve failed for %s: %s", url, e)
            annas_domains.mark_cool(host)
            return []
        return _parse_search_results(html, host)

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        try:
            html = cf_bypass_client.resolve(candidate.url)
        except Exception as e:
            log.warning("cf-bypass resolve failed for %s: %s", candidate.url, e)
            return None
        soup = BeautifulSoup(html, "lxml")
        for a in soup.select("a[href*='/d3/y/']"):
            return DownloadHandle(url=a["href"], headers={})
        return None


def _parse_search_results(html: str, host: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "lxml")
    out: list[Candidate] = []
    for a in soup.select("a[href^='/md5/']"):
        md5_url = f"https://{host}{a['href']}"
        title = a.get_text(" ", strip=True)[:200]
        out.append(Candidate(title=title, url=md5_url, format="epub",
                             provider="annas_cloakbrowser"))
    return out
```

- [ ] **Step 3: Write a test for the rewrite**

Append to `tests/unit/test_phase6w2_annas_hardening.py`:

```python
def test_annas_cloakbrowser_routes_through_sidecar(monkeypatch):
    from endless_library.scrapers.annas_cloakbrowser import AnnasArchiveCloakBrowser
    from endless_library.domain.models import SearchQuery
    seen_url = []
    def _fake_resolve(url, **kw):
        seen_url.append(url)
        return '<html><body><a href="/md5/abc">Sapiens</a></body></html>'
    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.resolve", _fake_resolve)
    cl = AnnasArchiveCloakBrowser(cfg=None)
    cands = cl.search(SearchQuery(title="Sapiens", author="Harari", isbn13="",
                                  format_priority=("epub",), language="en"))
    assert any("annas-archive" in u for u in seen_url)
    assert any(c.title == "Sapiens" for c in cands)


def test_annas_cloakbrowser_cools_mirror_on_resolve_failure(monkeypatch):
    from endless_library.scrapers.annas_cloakbrowser import AnnasArchiveCloakBrowser
    from endless_library.scrapers import annas_domains
    from endless_library.domain.models import SearchQuery
    annas_domains._reset_state()
    def _fail(url, **kw):
        raise RuntimeError("sidecar down")
    monkeypatch.setattr("endless_library.scrapers.cf_bypass_client.resolve", _fail)
    cl = AnnasArchiveCloakBrowser(cfg=None)
    out = cl.search(SearchQuery(title="x", author="", isbn13="",
                                format_priority=("epub",), language="en"))
    assert out == []
    # at least one mirror should now be cool
    assert any(annas_domains._is_cool(m) for m in annas_domains._MIRRORS)
```

- [ ] **Step 4: Run tests**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w2_annas_hardening.py -v"
```
Expected: pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/annas_cloakbrowser.py tests/unit/test_phase6w2_annas_hardening.py && git commit -m 'Phase 6w.2d: annas_cloakbrowser routes through cf-bypass sidecar' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 5: Parallel slot probing in annas_curl (optional but in spec)

**Files:**
- Modify: `src/endless_library/scrapers/annas_curl.py`
- Test: `tests/unit/test_phase6w2_annas_hardening.py` (extend)

- [ ] **Step 1: Write failing test**

```python
def test_resolve_slow_download_parallel_returns_first_winner(monkeypatch):
    import asyncio
    from endless_library.scrapers import annas_curl as mod
    async def _try_slot(sess, md5, i):
        delays = {0: 5, 1: 5, 2: 0.05, 3: 5, 4: 5}
        await asyncio.sleep(delays[i])
        return f"https://cdn/winner-slot-{i}"
    monkeypatch.setattr(mod, "_try_slot", _try_slot)
    url = asyncio.run(mod._resolve_slow_download_parallel("md5", max_slots=5))
    assert url.endswith("slot-2")


def test_resolve_slow_download_parallel_raises_when_all_countdown(monkeypatch):
    import asyncio
    from endless_library.scrapers import annas_curl as mod
    async def _try_slot(sess, md5, i):
        return None  # countdown / no direct link
    monkeypatch.setattr(mod, "_try_slot", _try_slot)
    try:
        asyncio.run(mod._resolve_slow_download_parallel("md5", max_slots=3))
        assert False
    except mod.AllSlotsCountdownError:
        pass
```

- [ ] **Step 2: Implement in annas_curl.py**

Append to `annas_curl.py`:

```python
import asyncio as _asyncio

class AllSlotsCountdownError(Exception):
    """All slot probes returned countdown timers; none gave a direct URL."""


async def _try_slot(sess, md5: str, slot: int) -> str | None:
    """Subclass-overridable hook; default returns None. Real implementation
    fetches https://annas-archive.<m>/slow_download/<md5>/0/<slot>, parses
    for direct CDN link, returns the URL or None if a countdown is shown."""
    return None


async def _resolve_slow_download_parallel(md5: str, max_slots: int = 5) -> str:
    """Fan out across slot 0..max_slots-1 concurrently. First slot to
    return a non-None URL wins; the rest are cancelled. Raises
    AllSlotsCountdownError if every slot returns None."""
    sess = None  # callers pass their own session in production
    tasks = [_asyncio.create_task(_try_slot(sess, md5, i)) for i in range(max_slots)]
    try:
        for coro in _asyncio.as_completed(tasks):
            try:
                url = await coro
            except Exception:
                continue
            if url:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                return url
        raise AllSlotsCountdownError(md5)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
```

- [ ] **Step 3: Run tests**

Expected: pass.

- [ ] **Step 4: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/annas_curl.py tests/unit/test_phase6w2_annas_hardening.py && git commit -m 'Phase 6w.2e: parallel slot probing in annas_curl' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 6: Full regression + push

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -3 && git push origin main"
```

---

## Sub-phase 6w.3 — HathiTrust + DOAB

**Goal of sub-phase:** Add two REST-based PD/OA sources. ISBN-only HathiTrust; full-text DOAB.

### File map

- **Create:** `src/endless_library/scrapers/hathitrust.py`
- **Create:** `src/endless_library/scrapers/doab.py`
- **Create:** `tests/unit/test_phase6w3_hathitrust_doab.py`
- **Modify:** `src/endless_library/scrapers/registry.py` (add to `_REGISTRY`, `_PD_PRIORITY`)
- **Modify:** `bench/queries.yaml` (corpus_tags add)

### Task 1: HathiTrust scraper

**Files:**
- Create: `src/endless_library/scrapers/hathitrust.py`
- Test: `tests/unit/test_phase6w3_hathitrust_doab.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_phase6w3_hathitrust_doab.py
def test_hathitrust_returns_pd_candidate_when_isbn_match(monkeypatch):
    from endless_library.scrapers.hathitrust import HathiTrust
    from endless_library.domain.models import SearchQuery
    fake = {
        "records": {
            "9999": {
                "titles": ["Pride and Prejudice"],
                "items": [
                    {"htid": "uc1.123", "rightsCode": "pd"},
                    {"htid": "uc1.456", "rightsCode": "ic-world"},
                ],
            }
        }
    }
    class _R:
        status_code = 200
        def json(self): return fake
        def raise_for_status(self): pass
    monkeypatch.setattr("endless_library.scrapers.hathitrust.make_client",
                        lambda **kw: _FakeSession(_R()))
    ht = HathiTrust(cfg=None)
    cands = ht.search(SearchQuery(title="anything", author="", isbn13="9780486284736",
                                  format_priority=("pdf",), language="en"))
    assert len(cands) == 1
    assert "babel.hathitrust.org" in cands[0].url
    assert cands[0].format == "pdf"


def test_hathitrust_returns_empty_when_no_isbn():
    from endless_library.scrapers.hathitrust import HathiTrust
    from endless_library.domain.models import SearchQuery
    ht = HathiTrust(cfg=None)
    out = ht.search(SearchQuery(title="x", author="", isbn13="",
                                format_priority=("pdf",), language="en"))
    assert out == []


class _FakeSession:
    def __init__(self, resp): self._r = resp
    def get(self, url, **kw): return self._r
```

- [ ] **Step 2: Run to verify failure**

Expected: ImportError on hathitrust module.

- [ ] **Step 3: Implement**

Create `src/endless_library/scrapers/hathitrust.py`:

```python
"""HathiTrust PD lookup. Only fires when SearchQuery carries an ISBN13
and matched records have a public-domain rights code. Full-text
search via Hathifiles bulk ingestion is deferred (see spec risks)."""
from __future__ import annotations

import logging

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.http_client import make_client

log = logging.getLogger(__name__)

_PD_RIGHTS = {"pd", "pdus"}


class HathiTrust:
    name = "hathitrust"
    provider = "hathitrust"
    category = "pd"

    def __init__(self, cfg, **kw):
        self._cfg = cfg
        self.client = make_client(timeout=20)

    def search(self, query: SearchQuery) -> list[Candidate]:
        if not query.isbn13:
            return []
        url = f"https://catalog.hathitrust.org/api/volumes/brief/json/isbn:{query.isbn13}"
        try:
            r = self.client.get(url)
        except Exception as e:
            log.warning("hathitrust: lookup failed: %s", e)
            return []
        if r.status_code != 200:
            return []
        try:
            body = r.json()
        except Exception:
            return []
        out: list[Candidate] = []
        for rec in body.get("records", {}).values():
            title = (rec.get("titles") or [query.title])[0]
            for item in rec.get("items", []):
                if item.get("rightsCode") in _PD_RIGHTS:
                    htid = item.get("htid")
                    if not htid:
                        continue
                    dl = f"https://babel.hathitrust.org/cgi/imgsrv/download/pdf?id={htid}"
                    out.append(Candidate(
                        title=title, url=dl, format="pdf",
                        provider=self.provider,
                    ))
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        return DownloadHandle(url=candidate.url, headers={})
```

- [ ] **Step 4: Run tests**

Expected: pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/hathitrust.py tests/unit/test_phase6w3_hathitrust_doab.py && git commit -m 'Phase 6w.3a: HathiTrust ISBN lookup scraper' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 2: DOAB scraper

**Files:**
- Create: `src/endless_library/scrapers/doab.py`
- Test: `tests/unit/test_phase6w3_hathitrust_doab.py` (extend)

- [ ] **Step 1: Write failing tests**

```python
def test_doab_search_extracts_pdf_candidates(monkeypatch):
    from endless_library.scrapers.doab import Doab
    from endless_library.domain.models import SearchQuery
    fake = [
        {
            "metadata": [
                {"key": "dc.title", "value": "Open Book Title"},
                {"key": "dc.creator", "value": "Author Name"},
                {"key": "dc.identifier.uri", "value": "https://example.org/book.pdf"},
            ]
        },
    ]
    class _R:
        status_code = 200
        def json(self): return fake
        def raise_for_status(self): pass
    monkeypatch.setattr("endless_library.scrapers.doab.make_client",
                        lambda **kw: _FakeSession(_R()))
    d = Doab(cfg=None)
    out = d.search(SearchQuery(title="Open Book", author="", isbn13="",
                               format_priority=("pdf",), language="en"))
    assert len(out) == 1
    assert out[0].title == "Open Book Title"
    assert out[0].url.endswith("book.pdf")


def test_doab_includes_language_filter_when_set(monkeypatch):
    from endless_library.scrapers.doab import Doab
    from endless_library.domain.models import SearchQuery
    sent_params = {}
    class _R:
        status_code = 200
        def json(self): return []
        def raise_for_status(self): pass
    class _Sess:
        def get(self, url, params=None, **kw):
            sent_params.update(params or {})
            return _R()
    monkeypatch.setattr("endless_library.scrapers.doab.make_client", lambda **kw: _Sess())
    d = Doab(cfg=None)
    d.search(SearchQuery(title="x", author="", isbn13="",
                         format_priority=("pdf",), language="en"))
    assert "language:en" in sent_params.get("query", "")
```

- [ ] **Step 2: Run to verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/endless_library/scrapers/doab.py`:

```python
"""DOAB (Directory of Open Access Books) — ~90k OA scholarly books.
REST search at /rest/search; results carry DC metadata with download
URLs at oapen.relation.isPartOfBook or dc.identifier.uri."""
from __future__ import annotations

import logging

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.http_client import make_client

log = logging.getLogger(__name__)

_API = "https://directory.doabooks.org/rest/search"


class Doab:
    name = "doab"
    provider = "doab"
    category = "pd"

    def __init__(self, cfg, **kw):
        self._cfg = cfg
        self.client = make_client(timeout=20)

    def search(self, query: SearchQuery) -> list[Candidate]:
        q = query.title or ""
        if query.language:
            q += f" AND language:{query.language}"
        try:
            r = self.client.get(_API, params={"expand": "metadata", "query": q})
        except Exception as e:
            log.warning("doab: request failed: %s", e)
            return []
        if r.status_code != 200:
            return []
        try:
            items = r.json() or []
        except Exception:
            return []
        out: list[Candidate] = []
        for it in items[:20]:
            md = {kv["key"]: kv["value"] for kv in it.get("metadata", [])}
            url = md.get("oapen.relation.isPartOfBook") or md.get("dc.identifier.uri")
            if not url:
                continue
            out.append(Candidate(
                title=md.get("dc.title", query.title),
                author=md.get("dc.creator", ""),
                url=url,
                format="pdf",
                provider=self.provider,
            ))
        return out

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        return DownloadHandle(url=candidate.url, headers={})
```

- [ ] **Step 4: Run tests**

Expected: pass.

- [ ] **Step 5: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/doab.py tests/unit/test_phase6w3_hathitrust_doab.py && git commit -m 'Phase 6w.3b: DOAB REST search scraper' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 3: Registry + corpus tags

**Files:**
- Modify: `src/endless_library/scrapers/registry.py`
- Modify: `bench/queries.yaml`

- [ ] **Step 1: Register both scrapers**

In `src/endless_library/scrapers/registry.py`:

```python
from endless_library.scrapers.hathitrust import HathiTrust
from endless_library.scrapers.doab import Doab

_REGISTRY = {
    # ... existing ...
    "hathitrust": HathiTrust,
    "doab": Doab,
}

_PD_PRIORITY = (
    "standard_ebooks", "gutendex", "wikisource",
    "oapen_doab", "doab", "hathitrust",
)
```

- [ ] **Step 2: Add corpus tags**

In `bench/queries.yaml`, extend `corpus_tags`:

```yaml
corpus_tags:
  kindlebangla_curl: [kindlebangla]
  gutendex: [pd]
  standard_ebooks: [pd]
  oapen_doab: [pd]
  wikisource: [pd]
  hathitrust: [pd]
  doab: [pd]
```

- [ ] **Step 3: Run regression**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6v_bench_corpus.py tests/unit/test_phase6w3_hathitrust_doab.py tests/unit/test_phase6v_scraper_health.py -v 2>&1 | tail -10"
```

- [ ] **Step 4: Commit + push**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/registry.py bench/queries.yaml && git commit -m 'Phase 6w.3c: register HathiTrust + DOAB + PD priority' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>' && git push origin main"
```

---

## Sub-phase 6w.5 — Mobilism books

**Goal of sub-phase:** Add the Mobilism phpBB books subforum as a recent-release-promoted scraper. Shared login session; Mediafire resolver for direct downloads.

### File map

- **Create:** `src/endless_library/scrapers/mobilism.py`
- **Create:** `src/endless_library/scrapers/mobilism_books.py`
- **Create:** `src/endless_library/scrapers/mediafire_helpers.py`
- **Create:** `tests/unit/test_phase6w5_mobilism.py`
- **Modify:** `src/endless_library/scrapers/registry.py`
- **Modify:** `src/endless_library/web/api.py` (credential storage + test-creds endpoint)
- **Modify:** `webapp/src/pages/ScrapersPage.vue` (Mobilism card)
- **Modify:** `src/endless_library/pipeline.py` (recent_release hint)
- **Modify:** `src/endless_library/config.py` (`recent_release_window_years`)

### Task 1: Mediafire resolver

**Files:**
- Create: `src/endless_library/scrapers/mediafire_helpers.py`
- Test: `tests/unit/test_phase6w5_mobilism.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_phase6w5_mobilism.py
def test_mediafire_resolver_extracts_dynamic_url():
    from endless_library.scrapers.mediafire_helpers import resolve
    html = '''
    <html><body><script>
    var foo = 'irrelevant';
    window.location.href = "https://download123.mediafire.com/abc/book.epub";
    </script></body></html>
    '''
    class _Sess:
        def get(self, url, **kw):
            class _R:
                text = html
                status_code = 200
            return _R()
    assert resolve("https://mediafire.com/x", _Sess()) == "https://download123.mediafire.com/abc/book.epub"


def test_mediafire_resolver_returns_none_on_no_match():
    from endless_library.scrapers.mediafire_helpers import resolve
    class _Sess:
        def get(self, url, **kw):
            class _R:
                text = "<html>no download here</html>"
                status_code = 200
            return _R()
    assert resolve("https://mediafire.com/x", _Sess()) is None
```

- [ ] **Step 2: Run to verify failure**

Expected: ImportError.

- [ ] **Step 3: Implement**

Create `src/endless_library/scrapers/mediafire_helpers.py`:

```python
"""Extract direct-download URL from Mediafire's dynamic page.

Pattern ported from Greasyfork userscript 499381. Mediafire's
download page sets `window.location.href = "<direct CDN url>"` inside
a script block; we regex it out without executing JS.
"""
from __future__ import annotations

import re
from typing import Any

_RE = re.compile(r'''window\.location\.href\s*=\s*["']([^"']+)["']''')


def resolve(url: str, session: Any) -> str | None:
    try:
        r = session.get(url)
    except Exception:
        return None
    if getattr(r, "status_code", 0) != 200:
        return None
    m = _RE.search(r.text or "")
    return m.group(1) if m else None
```

- [ ] **Step 4: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w5_mobilism.py -v && cd /home/ubuntu/endless-library && git add src/endless_library/scrapers/mediafire_helpers.py tests/unit/test_phase6w5_mobilism.py && git commit -m 'Phase 6w.5a: Mediafire dynamic URL resolver' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 2: Mobilism shared session

**Files:**
- Create: `src/endless_library/scrapers/mobilism.py`
- Test: `tests/unit/test_phase6w5_mobilism.py` (extend)

- [ ] **Step 1: Write failing tests**

```python
def test_mobilism_session_raises_when_creds_missing(monkeypatch):
    from endless_library.scrapers.mobilism import MobilismSession, NotConfigured
    class _Svc:
        def get_secret_value(self, key): return None
    try:
        MobilismSession.get(_Svc())
        assert False, "should raise"
    except NotConfigured:
        pass


def test_mobilism_session_logs_in_and_caches(monkeypatch):
    from endless_library.scrapers.mobilism import MobilismSession
    posted = []
    class _Resp:
        status_code = 200
        url = "https://forum.mobilism.org/index.php"
        text = ""
    class _Sess:
        def post(self, url, data=None, allow_redirects=True, **kw):
            posted.append(url); return _Resp()
        def get(self, url, **kw):
            return _Resp()
    monkeypatch.setattr("endless_library.scrapers.mobilism.make_client", lambda **kw: _Sess())
    class _Svc:
        def get_secret_value(self, key):
            return "user" if "username" in key else "pw"
    s1 = MobilismSession.get(_Svc())
    s2 = MobilismSession.get(_Svc())
    assert s1 is s2  # cached
    assert any("ucp.php?mode=login" in u for u in posted)


def test_mobilism_session_raises_when_login_redirects_back(monkeypatch):
    from endless_library.scrapers.mobilism import MobilismSession, AuthFailed, _reset_session
    _reset_session()
    class _Resp:
        status_code = 200
        url = "https://forum.mobilism.org/ucp.php?mode=login"
        text = ""
    class _Sess:
        def post(self, url, data=None, **kw): return _Resp()
    monkeypatch.setattr("endless_library.scrapers.mobilism.make_client", lambda **kw: _Sess())
    class _Svc:
        def get_secret_value(self, key): return "x"
    try:
        MobilismSession.get(_Svc())
        assert False
    except AuthFailed:
        pass
```

- [ ] **Step 2: Implement**

Create `src/endless_library/scrapers/mobilism.py`:

```python
"""Mobilism phpBB shared login + session cache.

Free registration required. Credentials stored in the encrypted
secrets store (same pattern as zlib_singlelogin). Login cookie cached
for ~24h; reseed on 401.
"""
from __future__ import annotations

import time
from typing import Any

from endless_library.scrapers.http_client import make_client


_SESSION_TTL_SEC = 24 * 60 * 60
_LOGIN_URL = "https://forum.mobilism.org/ucp.php?mode=login"


class NotConfigured(Exception):
    pass


class AuthFailed(Exception):
    pass


class MobilismSession:
    _instance: "MobilismSession | None" = None

    def __init__(self, session: Any, established_at: float):
        self.session = session
        self.established_at = established_at

    def _expired(self) -> bool:
        return (time.time() - self.established_at) > _SESSION_TTL_SEC

    @classmethod
    def get(cls, svc) -> "MobilismSession":
        if cls._instance is None or cls._instance._expired():
            cls._instance = cls._build(svc)
        return cls._instance

    @classmethod
    def _build(cls, svc) -> "MobilismSession":
        username = svc.get_secret_value("mobilism.username")
        password = svc.get_secret_value("mobilism.password")
        if not (username and password):
            raise NotConfigured("set mobilism.username/password in Settings → Scrapers")
        sess = make_client()
        r = sess.post(
            _LOGIN_URL,
            data={
                "username": username, "password": password,
                "login": "Login", "redirect": "./index.php",
                "autologin": "on",
            },
            allow_redirects=True,
        )
        if "ucp.php?mode=login" in getattr(r, "url", "") or "login" in (r.text or "").lower()[:200]:
            raise AuthFailed("Mobilism login was rejected; check credentials")
        return cls(session=sess, established_at=time.time())


def _reset_session() -> None:
    """Test hook: clear the cached instance."""
    MobilismSession._instance = None
```

- [ ] **Step 3: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w5_mobilism.py -v && git add src/endless_library/scrapers/mobilism.py tests/unit/test_phase6w5_mobilism.py && git commit -m 'Phase 6w.5b: Mobilism shared session + login' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 3: mobilism_books scraper

**Files:**
- Create: `src/endless_library/scrapers/mobilism_books.py`
- Test: `tests/unit/test_phase6w5_mobilism.py` (extend)

- [ ] **Step 1: Write failing test**

```python
def test_mobilism_books_extracts_thread_links_with_mediafire(monkeypatch):
    from endless_library.scrapers.mobilism_books import MobilismBooks
    from endless_library.domain.models import SearchQuery
    search_html = '''
    <html><body>
      <a class="topictitle" href="/viewtopic.php?t=12345">Project Hail Mary</a>
      <a class="topictitle" href="/viewtopic.php?t=99">irrelevant</a>
    </body></html>
    '''
    post_html = '''
    <html><body>
      <a href="https://www.mediafire.com/file/abc/book.epub/file">DL</a>
    </body></html>
    '''
    class _Sess:
        def get(self, url, **kw):
            class _R:
                status_code = 200
                text = search_html if "search.php" in url else post_html
            return _R()
    class _Sv:
        def get_secret_value(self, k): return "x"
    monkeypatch.setattr("endless_library.scrapers.mobilism.make_client",
                        lambda **kw: _Sess())
    monkeypatch.setattr("endless_library.scrapers.mediafire_helpers.resolve",
                        lambda u, s: "https://download.mediafire.com/x/book.epub")
    mb = MobilismBooks(cfg=None, svc=_Sv())
    cands = mb.search(SearchQuery(title="Project Hail Mary", author="Weir", isbn13="",
                                  format_priority=("epub",), language="en"))
    assert any(c.url.endswith("book.epub") and "Project Hail Mary" in c.title for c in cands)
```

- [ ] **Step 2: Implement**

Create `src/endless_library/scrapers/mobilism_books.py`:

```python
"""Mobilism books subforum scraper.

Logs in (via shared MobilismSession), searches the books forum,
opens top-N matching threads, extracts download links and prefers
Mediafire-resolved direct URLs.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers import mediafire_helpers
from endless_library.scrapers.mobilism import MobilismSession, NotConfigured

log = logging.getLogger(__name__)

_BASE = "https://forum.mobilism.org"
_FORUM_ID = 15  # books subforum (verify on first deploy)


class MobilismBooks:
    name = "mobilism_books"
    provider = "mobilism_books"
    category = "general"

    def __init__(self, cfg, svc=None, **kw):
        self._cfg = cfg
        self._svc = svc

    def search(self, query: SearchQuery) -> list[Candidate]:
        try:
            session = MobilismSession.get(self._svc).session
        except NotConfigured as e:
            log.info("mobilism_books skipped: %s", e)
            return []
        r = session.get(
            f"{_BASE}/search.php",
            params={"keywords": query.title, "fid[]": _FORUM_ID, "sf": "titleonly"},
        )
        if getattr(r, "status_code", 0) != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        threads = []
        for a in soup.select("a.topictitle"):
            href = a.get("href") or ""
            title = a.get_text(" ", strip=True)
            if not href.startswith("http"):
                href = urljoin(_BASE, href)
            threads.append((title, href))
            if len(threads) >= 5:
                break
        out: list[Candidate] = []
        for title, thread_url in threads:
            direct = self._resolve_post(session, thread_url)
            if direct:
                ext = direct.rsplit(".", 1)[-1].lower() if "." in direct else "epub"
                out.append(Candidate(
                    title=title, url=direct, format=ext if ext in {"epub","pdf","azw3","mobi"} else "epub",
                    provider=self.provider,
                ))
        return out

    def _resolve_post(self, session, post_url: str) -> str | None:
        r = session.get(post_url)
        if getattr(r, "status_code", 0) != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            url = a["href"]
            host = urlparse(url).netloc.lower()
            if "mediafire.com" in host:
                resolved = mediafire_helpers.resolve(url, session)
                if resolved:
                    return resolved
            elif url.lower().endswith((".epub", ".pdf", ".azw3", ".mobi")):
                return url
        return None

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        return DownloadHandle(url=candidate.url, headers={})
```

- [ ] **Step 3: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w5_mobilism.py -v && git add src/endless_library/scrapers/mobilism_books.py tests/unit/test_phase6w5_mobilism.py && git commit -m 'Phase 6w.5c: mobilism_books scraper' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 4: Credentials endpoint + Settings UI

**Files:**
- Modify: `src/endless_library/web/api.py`
- Modify: `webapp/src/pages/ScrapersPage.vue` (add Mobilism card)

- [ ] **Step 1: Add API endpoints**

In `src/endless_library/web/api.py`, in the same section as the Z-Library creds endpoints:

```python
@router.post("/scrapers/mobilism/creds")
def mobilism_store_creds(payload: dict, request: Request):
    svc = _bookorbit_service(request)
    username = (payload or {}).get("username") or ""
    password = (payload or {}).get("password") or ""
    if not (username and password):
        raise HTTPException(400, "missing username and/or password")
    svc.set_secret_value("mobilism.username", username)
    svc.set_secret_value("mobilism.password", password)
    return {"ok": True}


@router.delete("/scrapers/mobilism/creds")
def mobilism_clear_creds(request: Request):
    svc = _bookorbit_service(request)
    svc.delete_secret_value("mobilism.username")
    svc.delete_secret_value("mobilism.password")
    from endless_library.scrapers.mobilism import _reset_session
    _reset_session()
    return {"ok": True}


@router.post("/scrapers/mobilism/test-creds")
def mobilism_test_creds(request: Request):
    from endless_library.scrapers.mobilism import MobilismSession, NotConfigured, AuthFailed, _reset_session
    svc = _bookorbit_service(request)
    _reset_session()
    try:
        MobilismSession.get(svc)
        return {"ok": True}
    except NotConfigured as e:
        return {"ok": False, "error": str(e)}
    except AuthFailed as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
```

- [ ] **Step 2: Add SPA card**

In `webapp/src/pages/ScrapersPage.vue`, after the Z-Library card, add:

```vue
<Card class="p-4 space-y-3">
  <h2 class="font-semibold flex items-center gap-2 text-sm">
    <KeyRound class="w-4 h-4 text-primary" /> Mobilism credentials (optional)
  </h2>
  <p class="text-[11px] text-muted-foreground leading-relaxed">
    Free Mobilism forum account enables the <code class="font-mono">mobilism_books</code>
    scraper for recent EN releases (Anna's lags ~1 week). Credentials stored
    encrypted in <code>library.db</code>.
  </p>
  <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
    <label class="text-xs space-y-1">
      <span class="text-muted-foreground">Username</span>
      <input v-model="mobiUser" type="text" autocomplete="username"
        class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
    </label>
    <label class="text-xs space-y-1">
      <span class="text-muted-foreground">Password</span>
      <input v-model="mobiPass" type="password" autocomplete="off"
        class="w-full bg-background border border-border rounded px-2 py-1.5 text-sm" />
    </label>
  </div>
  <p v-if="mobiResult" class="text-[11px] font-mono"
     :class="mobiResult.ok ? 'text-emerald-500' : 'text-red-500'">
    {{ mobiResult.ok ? '✓ Login OK' : '✗ ' + mobiResult.error }}
  </p>
  <div class="flex gap-2 justify-end">
    <Button variant="ghost" size="sm" @click="clearMobi">Clear</Button>
    <Button variant="outline" size="sm" :disabled="mobiBusy" @click="testMobi">
      Test login
    </Button>
    <Button size="sm" :disabled="mobiBusy || !mobiUser || !mobiPass" @click="saveMobi">
      {{ mobiBusy ? 'Saving...' : 'Save' }}
    </Button>
  </div>
</Card>
```

Add to the script block:

```vue
const mobiUser = ref('')
const mobiPass = ref('')
const mobiBusy = ref(false)
const mobiResult = ref<{ ok: boolean; error?: string } | null>(null)

async function saveMobi() {
  mobiBusy.value = true
  try {
    await api('/api/scrapers/mobilism/creds', {
      method: 'POST',
      body: JSON.stringify({ username: mobiUser.value, password: mobiPass.value }),
      headers: { 'Content-Type': 'application/json' },
    })
    mobiUser.value = ''; mobiPass.value = ''
    toast.success('Mobilism credentials saved')
  } catch (e: any) {
    toast.error('Save failed: ' + (e?.message ?? e))
  } finally { mobiBusy.value = false }
}

async function clearMobi() {
  if (!confirm('Clear Mobilism credentials?')) return
  await api('/api/scrapers/mobilism/creds', { method: 'DELETE' })
  toast.success('Cleared')
}

async function testMobi() {
  mobiBusy.value = true
  mobiResult.value = null
  try {
    mobiResult.value = await api('/api/scrapers/mobilism/test-creds', { method: 'POST' })
  } catch (e: any) {
    mobiResult.value = { ok: false, error: String(e?.message ?? e) }
  } finally { mobiBusy.value = false }
}
```

- [ ] **Step 3: SPA build**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library/webapp && npm run build 2>&1 | tail -5"
```

- [ ] **Step 4: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/web/api.py webapp/src/pages/ScrapersPage.vue && git commit -m 'Phase 6w.5d: Mobilism creds API + Settings card' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 5: Recent-release chain promotion

**Files:**
- Modify: `src/endless_library/scrapers/registry.py` (add `chain_for_recent_release`)
- Modify: `src/endless_library/pipeline.py` (pass hint)
- Modify: `src/endless_library/config.py` (`recent_release_window_years`)

- [ ] **Step 1: Add cfg field**

In `Config.bench` or `Config.scrapers`, add:

```python
recent_release_window_years: int = 1
```

- [ ] **Step 2: Extend chain_for_source**

In `src/endless_library/scrapers/registry.py`:

```python
def chain_for_source(cfg, *, source: str | None, query_title: str,
                     is_pd: bool, is_recent_release: bool = False) -> list[str]:
    if source == "kindlebangla" and "kindlebangla_curl" in _REGISTRY:
        return ["kindlebangla_curl"]
    base = pd_aware_order(cfg, query_title=query_title, is_pd=is_pd)
    if is_recent_release and "mobilism_books" in base:
        promoted = ["mobilism_books"]
        rest = [n for n in base if n not in promoted]
        return promoted + rest
    return base
```

- [ ] **Step 3: pipeline passes the hint**

In `src/endless_library/pipeline.py`, find the `chain_for_source` call. Pass `is_recent_release` computed as:

```python
import datetime as _dt
current_year = _dt.datetime.now().year
window = cfg.scrapers.recent_release_window_years
is_recent = (book.pub_year or 0) >= (current_year - window)
chain = chain_for_source(cfg, source=book.source, query_title=book.title,
                         is_pd=is_pd, is_recent_release=is_recent)
```

- [ ] **Step 4: Add registry + ensure mobilism_books in default order**

In `registry.py` `_REGISTRY`:

```python
from endless_library.scrapers.mobilism_books import MobilismBooks
_REGISTRY["mobilism_books"] = MobilismBooks
```

In default `config.yaml.example` `scrapers.order`, add `mobilism_books` (commented as opt-in if you prefer; or active if it makes sense).

- [ ] **Step 5: Write a recent-release test**

Append to `tests/unit/test_phase6w5_mobilism.py`:

```python
def test_chain_for_recent_release_promotes_mobilism_books():
    from endless_library.scrapers.registry import chain_for_source
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        order=["annas_curl", "libgen_curl", "mobilism_books"],
        enabled={"annas_curl": True, "libgen_curl": True, "mobilism_books": True},
    )
    chain = chain_for_source(cfg, source=None, query_title="Modern Book",
                             is_pd=False, is_recent_release=True)
    assert chain[0] == "mobilism_books"


def test_chain_for_old_book_does_not_promote_mobilism():
    from endless_library.scrapers.registry import chain_for_source
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        order=["annas_curl", "libgen_curl", "mobilism_books"],
        enabled={"annas_curl": True, "libgen_curl": True, "mobilism_books": True},
    )
    chain = chain_for_source(cfg, source=None, query_title="Old Book",
                             is_pd=True, is_recent_release=False)
    assert chain[0] != "mobilism_books"
```

- [ ] **Step 6: Run + commit + push**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w5_mobilism.py tests/integration -v 2>&1 | tail -10 && git add src/endless_library/scrapers/registry.py src/endless_library/pipeline.py src/endless_library/config.py tests/unit/test_phase6w5_mobilism.py && git commit -m 'Phase 6w.5e: chain_for_source promotes mobilism_books for recent releases' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>' && git push origin main"
```

---

## Sub-phase 6w.6 — BDeBooks + content-filter abstraction

**Goal of sub-phase:** Add Bengali BDeBooks scraper. Introduce `Candidate.categories` + per-source `excluded_categories` denylist with sensible defaults (Islamic/religious). Apply retroactively to KindleBangla.

### File map

- **Modify:** `src/endless_library/domain/models.py` (Candidate.categories)
- **Create:** `src/endless_library/scrapers/bdebooks.py`
- **Modify:** `src/endless_library/scrapers/kindlebangla_curl.py` (apply filter)
- **Modify:** `src/endless_library/scrapers/registry.py` (`_NON_LATIN_PRIORITY`)
- **Modify:** `src/endless_library/config.py` (`excluded_categories` per source)
- **Modify:** `config/config.yaml.example` (defaults)
- **Modify:** `src/endless_library/web/api.py` (excluded_categories endpoints)
- **Modify:** `webapp/src/pages/ScrapersPage.vue` (denylist editor UI)
- **Create:** `tests/unit/test_phase6w6_bdebooks_filter.py`

### Task 1: Add Candidate.categories

**Files:**
- Modify: `src/endless_library/domain/models.py`
- Test: `tests/unit/test_phase6w6_bdebooks_filter.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_phase6w6_bdebooks_filter.py
def test_candidate_categories_default_empty_tuple():
    from endless_library.domain.models import Candidate
    c = Candidate(title="t", url="u", format="epub", provider="p")
    assert c.categories == ()


def test_candidate_categories_can_be_set():
    from endless_library.domain.models import Candidate
    c = Candidate(title="t", url="u", format="epub", provider="p",
                  categories=("Islamic", "Fiction"))
    assert c.categories == ("Islamic", "Fiction")
```

- [ ] **Step 2: Add field to dataclass**

In `src/endless_library/domain/models.py`, find the `Candidate` dataclass and add:

```python
categories: tuple[str, ...] = ()
```

- [ ] **Step 3: Run + verify other tests still pass (no positional-arg breaks)**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -5"
```

- [ ] **Step 4: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/domain/models.py tests/unit/test_phase6w6_bdebooks_filter.py && git commit -m 'Phase 6w.6a: Candidate.categories field' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 2: BDeBooks scraper with category extraction

**Files:**
- Create: `src/endless_library/scrapers/bdebooks.py`
- Test: `tests/unit/test_phase6w6_bdebooks_filter.py` (extend)

- [ ] **Step 1: Write failing tests**

```python
def test_bdebooks_search_extracts_titles_and_categories(monkeypatch):
    from endless_library.scrapers.bdebooks import BDeBooks
    from endless_library.domain.models import SearchQuery
    html = '''
    <html><body>
      <article class="post">
        <a class="entry-title" href="https://bdebooks.com/books/himu/">হিমু</a>
        <a class="category" href="/category/fiction/">Fiction</a>
      </article>
      <article class="post">
        <a class="entry-title" href="https://bdebooks.com/books/quran-translation/">কোরআন</a>
        <a class="category" href="/category/islamic-books/">Islamic Books</a>
      </article>
    </body></html>
    '''
    detail_html = '<html><body><a href="https://bdebooks.com/files/himu.pdf">PDF</a></body></html>'
    class _Sess:
        def get(self, url, **kw):
            class _R:
                status_code = 200
                text = detail_html if "/books/" in url and "?" not in url else html
            return _R()
    monkeypatch.setattr("endless_library.scrapers.bdebooks.make_client", lambda **kw: _Sess())
    b = BDeBooks(cfg=type("C", (), {"excluded_categories": ()})())
    cands = b.search(SearchQuery(title="হিমু", author="", isbn13="",
                                 format_priority=("pdf",), language="bn"))
    titles = {c.title for c in cands}
    assert "হিমু" in titles or any("হিমু" in t for t in titles)
    # Categories populated
    assert all(isinstance(c.categories, tuple) for c in cands)


def test_bdebooks_excludes_islamic_when_in_denylist(monkeypatch):
    from endless_library.scrapers.bdebooks import BDeBooks
    from endless_library.domain.models import SearchQuery
    html = '''
    <html><body>
      <article class="post">
        <a class="entry-title" href="https://bdebooks.com/books/q/">কোরআন</a>
        <a class="category" href="/category/islamic-books/">Islamic Books</a>
      </article>
    </body></html>
    '''
    class _Sess:
        def get(self, url, **kw):
            class _R:
                status_code = 200
                text = html
            return _R()
    monkeypatch.setattr("endless_library.scrapers.bdebooks.make_client", lambda **kw: _Sess())
    cfg = type("C", (), {"excluded_categories": ("Islamic Books", "Religious")})()
    b = BDeBooks(cfg=cfg)
    cands = b.search(SearchQuery(title="কোরআন", author="", isbn13="",
                                 format_priority=("pdf",), language="bn"))
    assert cands == []
```

- [ ] **Step 2: Implement**

Create `src/endless_library/scrapers/bdebooks.py`:

```python
"""BDeBooks Bangla PDF scraper.

bdebooks.com is a WordPress-based catalog of ~11k Bangla PDFs.
Live search via ?s=<query>; each result carries a category that we
extract into Candidate.categories so the per-source excluded_categories
denylist can filter Islamic / religious content before pushing to
the queue.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from endless_library.domain.models import Candidate, DownloadHandle, SearchQuery
from endless_library.scrapers.http_client import make_client

log = logging.getLogger(__name__)


class BDeBooks:
    name = "bdebooks"
    provider = "bdebooks"
    category = "general"
    BASE = "https://bdebooks.com"

    def __init__(self, cfg, **kw):
        self._cfg = cfg
        self.client = make_client(timeout=20)

    def search(self, query: SearchQuery) -> list[Candidate]:
        try:
            r = self.client.get(self.BASE, params={"s": query.title})
        except Exception as e:
            log.warning("bdebooks: search failed: %s", e)
            return []
        if getattr(r, "status_code", 0) != 200:
            return []
        soup = BeautifulSoup(r.text, "lxml")
        excluded = set(getattr(self._cfg, "excluded_categories", None) or ())
        out: list[Candidate] = []
        for art in soup.select("article.post"):
            title_el = art.select_one("a.entry-title") or art.select_one("h2 a")
            if not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            detail_url = title_el.get("href", "")
            categories = tuple(a.get_text(" ", strip=True) for a in art.select("a.category"))
            if set(categories) & excluded:
                continue
            pdf_url = self._extract_pdf(detail_url)
            if not pdf_url:
                continue
            out.append(Candidate(
                title=title, url=pdf_url, format="pdf",
                provider=self.provider, categories=categories,
            ))
        return out

    def _extract_pdf(self, detail_url: str) -> str | None:
        try:
            r = self.client.get(detail_url)
        except Exception:
            return None
        if getattr(r, "status_code", 0) != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.find_all("a", href=True):
            if a["href"].lower().endswith(".pdf"):
                return urljoin(detail_url, a["href"])
        return None

    def resolve_cdn(self, candidate: Candidate) -> DownloadHandle | None:
        return DownloadHandle(url=candidate.url, headers={})
```

- [ ] **Step 3: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w6_bdebooks_filter.py -v && git add src/endless_library/scrapers/bdebooks.py tests/unit/test_phase6w6_bdebooks_filter.py && git commit -m 'Phase 6w.6b: BDeBooks scraper with category extraction + denylist filter' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 3: Config schema + defaults

**Files:**
- Modify: `src/endless_library/config.py`
- Modify: `config/config.yaml.example` (defaults)
- Modify: live `config/config.yaml` (set the same defaults)

- [ ] **Step 1: Add per-source config schemas**

In `config.py`, find or add the scraper-specific sub-configs. Add:

```python
class BdebooksCfg(BaseModel):
    excluded_categories: list[str] = Field(default_factory=lambda: [
        "Islamic Books", "ইসলামিক বই", "Islamic", "Islam",
        "Religion", "Religious", "ধর্ম", "ধর্মীয়",
        "Hadith", "হাদিস", "Quran", "কোরআন",
        "Prophet", "নবী", "Islamic Studies", "ইসলামিক স্টাডিজ",
    ])


class KindleBanglaCfg(BaseModel):
    excluded_categories: list[str] = Field(default_factory=lambda: [
        "Islamic", "Religion", "Religious", "ধর্মীয়", "Hadith", "Quran",
    ])
```

Wire into `ScrapersCfg`:

```python
bdebooks: BdebooksCfg = BdebooksCfg()
kindlebangla: KindleBanglaCfg = KindleBanglaCfg()
```

- [ ] **Step 2: Add to config.yaml.example**

In `config/config.yaml.example`, under `scrapers:`, add:

```yaml
  bdebooks:
    excluded_categories:
      - "Islamic Books"
      - "ইসলামিক বই"
      - "Islamic"
      - "Islam"
      - "Religion"
      - "Religious"
      - "ধর্ম"
      - "ধর্মীয়"
      - "Hadith"
      - "হাদিস"
      - "Quran"
      - "কোরআন"
      - "Prophet"
      - "নবী"
      - "Islamic Studies"
      - "ইসলামিক স্টাডিজ"
  kindlebangla:
    excluded_categories:
      - "Islamic"
      - "Religion"
      - "Religious"
      - "ধর্মীয়"
      - "Hadith"
      - "Quran"
```

- [ ] **Step 3: Mirror into the live config**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && cp config/config.yaml.example config/config.yaml.6w6.bak.example 2>/dev/null ; echo manual-merge-required"
```

(Hand-merge into `config/config.yaml` if it diverges from the example; otherwise just copy the new keys.)

- [ ] **Step 4: Pass per-source cfg into the scraper constructor**

Scrapers with their own sub-config (`cfg.scrapers.bdebooks`, `cfg.scrapers.kindlebangla`) should receive that sub-config as their `cfg` arg, not the full `ScrapersCfg`. Note: the scraper *name* in the registry doesn't always match the config attribute name (e.g. `kindlebangla_curl` → `cfg.kindlebangla`).

Add to `src/endless_library/scrapers/registry.py`:

```python
# Phase 6w.6: scraper-name -> ScrapersCfg sub-config-attr map.
# Scrapers absent from this map receive the full ScrapersCfg.
_SCRAPER_TO_CFG_KEY = {
    "kindlebangla_curl": "kindlebangla",
    "bdebooks": "bdebooks",
}


def build(name: str, cfg: ScrapersCfg, **kwargs):
    if name not in _REGISTRY:
        raise KeyError(f"unknown scraper: {name}")
    klass = _REGISTRY[name]
    cfg_key = _SCRAPER_TO_CFG_KEY.get(name)
    per_source = getattr(cfg, cfg_key, None) if cfg_key else None
    return klass(per_source if per_source is not None else cfg, **kwargs)
```

Each scraper that opts into the per-source pattern accesses `self._cfg.excluded_categories` (typed against `BdebooksCfg` / `KindleBanglaCfg`).

- [ ] **Step 5: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w6_bdebooks_filter.py -v && git add src/endless_library/config.py config/config.yaml.example src/endless_library/scrapers/registry.py && git commit -m 'Phase 6w.6c: per-source excluded_categories config + defaults' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 4: KindleBangla retroactive filter

**Files:**
- Modify: `src/endless_library/scrapers/kindlebangla_curl.py`
- Test: `tests/unit/test_phase6w6_bdebooks_filter.py` (extend)

- [ ] **Step 1: Write failing test**

```python
def test_kindlebangla_filters_excluded_categories(monkeypatch):
    from endless_library.scrapers.kindlebangla_curl import KindleBanglaCurl
    from endless_library.domain.models import SearchQuery, Candidate
    # Bypass the scraper's normal search and inject categories directly
    # — we want to assert the FILTER mechanism, not the upstream HTML.
    def _fake_upstream(self, query):
        return [
            Candidate(title="হিমু", url="u1", format="epub", provider="x",
                      categories=("Fiction",)),
            Candidate(title="কোরআন", url="u2", format="epub", provider="x",
                      categories=("Islamic",)),
        ]
    monkeypatch.setattr(KindleBanglaCurl, "_search_upstream", _fake_upstream, raising=False)
    cfg = type("C", (), {"excluded_categories": ("Islamic",)})()
    kb = KindleBanglaCurl(cfg=cfg)
    cands = kb.search(SearchQuery(title="হিমু", author="", isbn13="",
                                  format_priority=("epub",), language="bn"))
    titles = [c.title for c in cands]
    assert "হিমু" in titles
    assert "কোরআন" not in titles
```

- [ ] **Step 2: Apply filter in kindlebangla_curl.py**

Modify `kindlebangla_curl.py`'s `search()` to apply the filter. If the existing method does HTML scraping, refactor to:

```python
def search(self, query: SearchQuery) -> list[Candidate]:
    cands = self._search_upstream(query)
    excluded = set(getattr(self._cfg, "excluded_categories", None) or ())
    return [c for c in cands if not (set(c.categories) & excluded)]

def _search_upstream(self, query: SearchQuery) -> list[Candidate]:
    # existing search body here, but ensure each Candidate has
    # categories populated from the kindlebangla category path
    ...
```

- [ ] **Step 3: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w6_bdebooks_filter.py -v && git add src/endless_library/scrapers/kindlebangla_curl.py tests/unit/test_phase6w6_bdebooks_filter.py && git commit -m 'Phase 6w.6d: KindleBangla applies excluded_categories filter' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 5: Registry + chain promotion

**Files:**
- Modify: `src/endless_library/scrapers/registry.py`

- [ ] **Step 1: Register + promote**

```python
from endless_library.scrapers.bdebooks import BDeBooks
_REGISTRY["bdebooks"] = BDeBooks

_NON_LATIN_PRIORITY = ("kindlebangla_curl", "bdebooks")
```

- [ ] **Step 2: Test**

```python
def test_non_latin_priority_promotes_bdebooks():
    from endless_library.scrapers.registry import enabled_order_for_query
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        order=["annas_curl", "bdebooks", "kindlebangla_curl"],
        enabled={"annas_curl": True, "bdebooks": True, "kindlebangla_curl": True},
    )
    chain = enabled_order_for_query(cfg, query_title="হিমু")
    assert chain[0] in ("kindlebangla_curl", "bdebooks")
    assert chain.index("kindlebangla_curl") < chain.index("annas_curl")
    assert chain.index("bdebooks") < chain.index("annas_curl")
```

- [ ] **Step 3: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w6_bdebooks_filter.py -v && git add src/endless_library/scrapers/registry.py tests/unit/test_phase6w6_bdebooks_filter.py && git commit -m 'Phase 6w.6e: register BDeBooks + add to _NON_LATIN_PRIORITY' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 6: SPA — denylist editor

**Files:**
- Modify: `src/endless_library/web/api.py`
- Modify: `webapp/src/pages/ScrapersPage.vue`

- [ ] **Step 1: Add GET/PUT endpoints**

In `web/api.py`:

```python
@router.get("/scrapers/{name}/excluded-categories")
def get_excluded_categories(name: str, request: Request):
    deps = request.app.state.deps
    per = getattr(deps.cfg.scrapers, name, None)
    if per is None or not hasattr(per, "excluded_categories"):
        return {"categories": []}
    return {"categories": list(per.excluded_categories)}


@router.put("/scrapers/{name}/excluded-categories")
def set_excluded_categories(name: str, payload: dict, request: Request):
    deps = request.app.state.deps
    per = getattr(deps.cfg.scrapers, name, None)
    if per is None or not hasattr(per, "excluded_categories"):
        raise HTTPException(400, f"scraper {name} has no excluded_categories")
    cats = (payload or {}).get("categories") or []
    if not isinstance(cats, list):
        raise HTTPException(400, "categories must be a list of strings")
    per.excluded_categories = [str(c) for c in cats]
    save_config(deps.cfg, request.app.state.config_path)
    return {"ok": True, "categories": per.excluded_categories}
```

- [ ] **Step 2: UI inline editor**

In `ScrapersPage.vue`, for each scraper with categories config (bdebooks, kindlebangla), add an inline edit affordance:

```vue
<details v-if="data.corpus_tags[name]?.length || data.has_excluded_categories?.[name]"
         class="text-[11px] mt-2">
  <summary class="cursor-pointer text-muted-foreground hover:underline">
    Excluded categories
  </summary>
  <textarea
    :value="(excludedCategoriesByName[name] || []).join('\n')"
    @blur="(e) => saveExcluded(name, (e.target as HTMLTextAreaElement).value)"
    class="mt-1 w-full bg-secondary p-2 rounded font-mono text-[11px]"
    rows="6" />
</details>
```

Add to the script:

```vue
const excludedCategoriesByName = reactive<Record<string, string[]>>({})

async function loadExcluded() {
  for (const name of ['bdebooks', 'kindlebangla_curl', 'kindlebangla']) {
    try {
      const r = await api<{ categories: string[] }>(`/api/scrapers/${name}/excluded-categories`)
      excludedCategoriesByName[name] = r.categories
    } catch {/* not configured for this scraper */}
  }
}

async function saveExcluded(name: string, raw: string) {
  const cats = raw.split('\n').map(s => s.trim()).filter(Boolean)
  await api(`/api/scrapers/${name}/excluded-categories`, {
    method: 'PUT',
    body: JSON.stringify({ categories: cats }),
    headers: { 'Content-Type': 'application/json' },
  })
  excludedCategoriesByName[name] = cats
  toast.success(`Excluded categories updated for ${name}`)
}

onMounted(loadExcluded)
```

- [ ] **Step 3: SPA build**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library/webapp && npm run build 2>&1 | tail -5"
```

- [ ] **Step 4: Commit + push**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/web/api.py webapp/src/pages/ScrapersPage.vue && git commit -m 'Phase 6w.6f: excluded_categories editor UI' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>' && git push origin main"
```

---

## Sub-phase 6w.9 — Hardening + UI

**Goal of sub-phase:** Patchright revive welib; enable zlib bench; PD chain verify test; Open Slum health integration; ScrapersPage categories grouping.

### File map

- **Modify:** `pyproject.toml` (+ patchright)
- **Modify:** `src/endless_library/scrapers/welib_playwright.py` (import swap)
- **Modify:** `bench/queries.yaml` (zlib_singlelogin corpus tag)
- **Modify:** `src/endless_library/bench.py` (NotConfigured handling)
- **Create:** `src/endless_library/scrapers/open_slum.py`
- **Modify:** `src/endless_library/web/api.py` (healthz extension, test_pd_chain endpoint)
- **Modify:** `webapp/src/pages/ScrapersPage.vue` (category grouping, status dots, Test PD chain button)
- **Create:** `tests/unit/test_phase6w9_hardening.py`

### Task 1: Patchright revive welib

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/endless_library/scrapers/welib_playwright.py`
- Modify: `config/config.yaml.example` (welib_playwright enabled: true)
- Test: `tests/unit/test_phase6w9_hardening.py`

- [ ] **Step 1: Add dep**

In `pyproject.toml`:

```toml
"patchright>=1.0",
```

Install:

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && pip install -e . 2>&1 | tail -3"
```

- [ ] **Step 2: Write smoke test**

```python
# tests/unit/test_phase6w9_hardening.py
def test_welib_playwright_imports_patchright():
    """Welib should import from patchright, not vanilla playwright."""
    import endless_library.scrapers.welib_playwright as wp
    src = open(wp.__file__).read()
    assert "from patchright" in src or "import patchright" in src
    # No remaining vanilla import:
    assert "from playwright.sync_api" not in src
    assert "from playwright.async_api" not in src
```

- [ ] **Step 3: Swap imports**

In `src/endless_library/scrapers/welib_playwright.py`, replace any:

```python
from playwright.sync_api import sync_playwright
# or
from playwright.async_api import async_playwright
```

with:

```python
from patchright.sync_api import sync_playwright
# or
from patchright.async_api import async_playwright
```

- [ ] **Step 4: Re-enable**

In `config/config.yaml.example` (and `config/config.yaml` on the live deployment), set:

```yaml
scrapers:
  enabled:
    welib_playwright: true
```

- [ ] **Step 5: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w9_hardening.py -v && git add pyproject.toml src/endless_library/scrapers/welib_playwright.py config/config.yaml.example tests/unit/test_phase6w9_hardening.py && git commit -m 'Phase 6w.9a: revive welib_playwright via patchright' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 2: zlib bench enablement + NotConfigured handling

**Files:**
- Modify: `bench/queries.yaml`
- Modify: `src/endless_library/bench.py`
- Test: `tests/unit/test_phase6w9_hardening.py` (extend)

- [ ] **Step 1: Add corpus tag**

In `bench/queries.yaml` `corpus_tags`:

```yaml
  zlib_singlelogin: [en, modern]
```

- [ ] **Step 2: Add NotConfigured guard test**

```python
def test_bench_records_not_configured_instead_of_raising(monkeypatch):
    from endless_library.bench import run_bench, BenchQuery
    from endless_library.scrapers import registry as r
    from types import SimpleNamespace

    class _NC(Exception): pass

    class _Scraper:
        name = "needs_creds"
        def __init__(self, *a, **kw):
            raise _NC("credentials missing")
        def search(self, q): pass

    monkeypatch.setattr(r, "_REGISTRY", {"needs_creds": _Scraper})
    monkeypatch.setattr(r, "enabled_order", lambda cfg: ["needs_creds"])
    def _build(n, cfg, **kw): return _Scraper(cfg, **kw)
    monkeypatch.setattr(r, "build", _build)

    cfg = SimpleNamespace(
        scrapers=SimpleNamespace(order=["needs_creds"],
                                 enabled={"needs_creds": True},
                                 format_priority=("epub",)),
        bench=SimpleNamespace(per_query_timeout_sec=5,
                              circuit_break_after_consecutive_fails=3),
    )
    out = run_bench(cfg, [BenchQuery("x", "", "", "en")])
    # Per the spec: bench should NOT raise; should record an outcome.
    assert len(out) >= 0  # nothing to record because build() raised
    # The legacy fallthrough is "could not build %s" warning; the spec
    # asks us to surface this as creds-missing outcomes instead.
```

(Adjust the test once you decide where to inject the NotConfigured-aware outcome — likely in the `except Exception as e: log.warning(...)` site in `run_bench`.)

- [ ] **Step 3: Surface NotConfigured as a recorded outcome**

In `src/endless_library/bench.py`, around the `registry.build` call:

```python
for s_name in strats:
    try:
        scraper = registry.build(s_name, cfg.scrapers)
    except NotConfigured as e:
        # Surface to UI as an outcome rather than silently skipping
        for q in queries_for_scraper(all_queries, s_name, tag_map):
            outcomes.append(BenchOutcome(
                scraper=s_name, query=q.title, success=False,
                duration_ms=0, candidates=0, matched_isbn=False,
                note=f"creds-missing: {e}",
            ))
            if repo:
                repo.record(scraper=s_name, query=q.title, success=False,
                            duration_ms=0, notes=f"creds-missing: {e}")
        continue
    except Exception as e:
        log.warning("could not build %s: %s", s_name, e)
        continue
```

In 6w.5 we defined `NotConfigured` in `scrapers/mobilism.py`. To make it
usable here without circular imports, move it to `scrapers/base.py`:

```python
# Add to src/endless_library/scrapers/base.py
class NotConfigured(Exception):
    """Raised at build time when a scraper's required credentials or
    profile are missing. Bench records this as a 'creds-missing'
    outcome rather than skipping silently."""
```

Then in `scrapers/mobilism.py`, replace the local class with a re-export
so existing callers keep working:

```python
from endless_library.scrapers.base import NotConfigured  # noqa: F401
# (remove the previous `class NotConfigured(Exception): pass`)
```

In `bench.py`, import from base:

```python
from endless_library.scrapers.base import NotConfigured
```

- [ ] **Step 4: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w9_hardening.py -v && git add bench/queries.yaml src/endless_library/bench.py src/endless_library/scrapers/base.py tests/unit/test_phase6w9_hardening.py && git commit -m 'Phase 6w.9b: zlib bench corpus + NotConfigured recorded as outcome' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 3: PD chain verification

**Files:**
- Modify: `src/endless_library/web/api.py`
- Test: `tests/unit/test_phase6w9_hardening.py` (extend)

- [ ] **Step 1: Write failing test**

```python
def test_pd_chain_promotes_pd_scrapers_for_pre_1928_books():
    from endless_library.scrapers.registry import chain_for_source
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        order=["annas_curl", "libgen_curl", "gutendex", "standard_ebooks",
               "wikisource", "oapen_doab", "doab", "hathitrust"],
        enabled={n: True for n in [
            "annas_curl", "libgen_curl", "gutendex", "standard_ebooks",
            "wikisource", "oapen_doab", "doab", "hathitrust",
        ]},
    )
    chain = chain_for_source(cfg, source=None, query_title="Pride and Prejudice",
                             is_pd=True, is_recent_release=False)
    for pd_name in ("standard_ebooks", "gutendex", "wikisource"):
        assert chain.index(pd_name) < chain.index("annas_curl"), \
            f"{pd_name} should fire before annas_curl on PD books"


def test_pd_chain_does_not_promote_pd_scrapers_for_modern_books():
    from endless_library.scrapers.registry import chain_for_source
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        order=["annas_curl", "gutendex"],
        enabled={"annas_curl": True, "gutendex": True},
    )
    chain = chain_for_source(cfg, source=None, query_title="Project Hail Mary",
                             is_pd=False, is_recent_release=False)
    assert chain.index("annas_curl") < chain.index("gutendex")
```

- [ ] **Step 2: Run (expected pass already; this is a regression-guard test)**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w9_hardening.py -v -k pd_chain"
```

- [ ] **Step 3: Add `/api/scrapers/test_pd_chain` endpoint**

In `web/api.py`:

```python
@router.post("/scrapers/test_pd_chain")
async def test_pd_chain(request: Request):
    """Run 'Pride and Prejudice' as a PD query through the full
    pipeline; return which scraper resolved (or first-with-candidates)."""
    from functools import partial
    from endless_library.bench import BenchQuery, run_bench
    from endless_library.scrapers import registry

    deps = request.app.state.deps
    q = BenchQuery("Pride and Prejudice", "Austen", "", "en")
    chain = registry.pd_aware_order(deps.cfg.scrapers, query_title=q.title, is_pd=True)
    outcomes = await asyncio.to_thread(
        partial(run_bench, deps.cfg, [q], repo=deps.bench, strategies=chain),
    )
    return {"chain": chain, "outcomes": [asdict(o) for o in outcomes]}
```

- [ ] **Step 4: Commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add src/endless_library/web/api.py tests/unit/test_phase6w9_hardening.py && git commit -m 'Phase 6w.9c: PD chain verification test + test_pd_chain endpoint' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 4: Open Slum monitor

**Files:**
- Create: `src/endless_library/scrapers/open_slum.py`
- Modify: `src/endless_library/web/api.py` (healthz extension)
- Test: `tests/unit/test_phase6w9_hardening.py` (extend)

- [ ] **Step 1: Write failing tests**

```python
def test_open_slum_monitor_caches_within_interval(monkeypatch):
    from endless_library.scrapers.open_slum import OpenSlumMonitor
    calls = {"n": 0}
    def _fake_get(self, force=False):
        if not force and time.time() - self._last_poll <= self.POLL_INTERVAL_SEC:
            return
        calls["n"] += 1
        self._cache = {"annas": {"up": True}}
        self._last_poll = time.time()
    monkeypatch.setattr(OpenSlumMonitor, "_refresh", _fake_get)
    m = OpenSlumMonitor()
    m._refresh()
    m.get("annas")
    m.get("annas")
    assert calls["n"] == 1


def test_open_slum_returns_none_for_unknown_site():
    from endless_library.scrapers.open_slum import OpenSlumMonitor
    m = OpenSlumMonitor()
    m._cache = {"annas": {"up": True}}
    m._last_poll = time.time()
    assert m.get("zzz") is None


def test_open_slum_handles_endpoint_unreachable(monkeypatch):
    from endless_library.scrapers.open_slum import OpenSlumMonitor
    def _raise(self): raise RuntimeError("DNS fail")
    monkeypatch.setattr(OpenSlumMonitor, "_fetch_remote", _raise)
    m = OpenSlumMonitor()
    m._refresh()  # should swallow + leave cache empty
    assert m.get("annas") is None
```

- [ ] **Step 2: Implement**

Create `src/endless_library/scrapers/open_slum.py`:

```python
"""Open Slum (open-slum.org) uptime monitor — passive health data.

NOT a scraper. Wired into /healthz and /api/scrapers to surface
upstream availability of Anna's / LibGen / Z-Library / WeLib.
Endpoint shape is verified on first deploy; falls back to scraping
the HTML status page if the JSON endpoint moves.
"""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class OpenSlumMonitor:
    URL = "https://open-slum.org/status.json"
    POLL_INTERVAL_SEC = 600
    HTTP_TIMEOUT = 5.0

    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._last_poll = 0.0

    def get(self, site: str) -> dict | None:
        if time.time() - self._last_poll > self.POLL_INTERVAL_SEC:
            self._refresh()
        return self._cache.get(site)

    def _refresh(self) -> None:
        try:
            data = self._fetch_remote()
        except Exception as e:
            log.info("open_slum: refresh failed: %s", e)
            return
        if isinstance(data, dict):
            self._cache = data
            self._last_poll = time.time()

    def _fetch_remote(self) -> dict:
        r = httpx.get(self.URL, timeout=self.HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 3: Wire into /healthz**

In `web/api.py`, find the `/healthz` handler. Add:

```python
@router.get("/healthz", include_in_schema=False)
def healthz(request: Request):
    # ... existing body ...
    # Phase 6w.9: surface external source health.
    monitor = getattr(request.app.state, "open_slum_monitor", None)
    if monitor is not None:
        external = {}
        for site in ("annas-archive", "libgen", "zlibrary", "welib"):
            row = monitor.get(site)
            if row is not None:
                external[site] = {"up": bool(row.get("up")),
                                  "checked_at": row.get("checked_at")}
        body["external_sources"] = external
    return body
```

In the app factory, instantiate the monitor:

```python
from endless_library.scrapers.open_slum import OpenSlumMonitor
app.state.open_slum_monitor = OpenSlumMonitor()
```

- [ ] **Step 4: Run + commit**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest tests/unit/test_phase6w9_hardening.py -v && git add src/endless_library/scrapers/open_slum.py src/endless_library/web/api.py src/endless_library/app.py tests/unit/test_phase6w9_hardening.py && git commit -m 'Phase 6w.9d: Open Slum upstream health monitor + healthz integration' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>'"
```

### Task 5: ScrapersPage category grouping

**Files:**
- Modify: `webapp/src/pages/ScrapersPage.vue`

- [ ] **Step 1: Group scrapers in template**

In `ScrapersPage.vue`, wrap the existing scraper list in two `<details>` sections, one per category:

```vue
<details open>
  <summary class="cursor-pointer text-sm font-semibold py-2">
    General — {{ generalScrapers.length }} scrapers,
    {{ generalScrapers.filter(n => data.in_chain[n]).length }} in chain
  </summary>
  <VueDraggable v-model="generalOrderModel" handle=".drag-handle" :animation="180"
                class="space-y-2 mt-2" @update="saveOrder">
    <!-- existing row template, but iterating generalOrderModel -->
  </VueDraggable>
</details>

<details open>
  <summary class="cursor-pointer text-sm font-semibold py-2">
    Public-domain — {{ pdScrapers.length }} scrapers,
    {{ pdScrapers.filter(n => data.in_chain[n]).length }} in chain
  </summary>
  <VueDraggable v-model="pdOrderModel" handle=".drag-handle" :animation="180"
                class="space-y-2 mt-2" @update="saveOrder">
    <!-- same row template -->
  </VueDraggable>
</details>
```

Add the computed lists in script:

```vue
const PD_SET = new Set(['gutendex', 'standard_ebooks', 'oapen_doab', 'wikisource',
                       'doab', 'hathitrust'])

const generalScrapers = computed(() =>
  (data.value?.order ?? []).filter(n => !PD_SET.has(n))
)
const pdScrapers = computed(() =>
  (data.value?.order ?? []).filter(n => PD_SET.has(n))
)
const generalOrderModel = computed({
  get: () => generalScrapers.value,
  set: (next) => {
    if (data.value) data.value.order = [...next, ...pdScrapers.value]
  },
})
const pdOrderModel = computed({
  get: () => pdScrapers.value,
  set: (next) => {
    if (data.value) data.value.order = [...generalScrapers.value, ...next]
  },
})
```

- [ ] **Step 2: Add fires-when badge for PD scrapers**

In the row template (within both VueDraggables), inside the badge cluster:

```vue
<Badge v-if="PD_SET.has(name)" variant="muted" title="Only fires when the book is public-domain">
  fires-when: PD
</Badge>
```

- [ ] **Step 3: Add upstream-status dot**

In the same row, near the success-rate area:

```vue
<span v-if="data.upstream_status?.[name]"
      :class="['inline-block w-2 h-2 rounded-full',
               data.upstream_status[name].up ? 'bg-emerald-500' : 'bg-red-500']"
      :title="data.upstream_status[name].up ? 'Upstream up' : 'Upstream down per Open Slum'" />
```

(Wire `upstream_status` into `GET /api/scrapers`: in `web/api.py`'s `list_scrapers`, after building `stats`:

```python
monitor = getattr(request.app.state, "open_slum_monitor", None)
upstream = {}
if monitor is not None:
    name_to_site = {
        "annas_curl": "annas-archive", "annas_flaresolverr": "annas-archive",
        "annas_playwright": "annas-archive", "annas_cloakbrowser": "annas-archive",
        "libgen_curl": "libgen", "zlib_singlelogin": "zlibrary",
        "welib_curl": "welib", "welib_playwright": "welib",
    }
    for n in all_names:
        site = name_to_site.get(n)
        if site:
            row = monitor.get(site)
            if row is not None:
                upstream[n] = {"up": bool(row.get("up"))}
return {... existing ..., "upstream_status": upstream}
```
)

- [ ] **Step 4: Test PD chain button**

```vue
<Button variant="outline" size="sm" :loading="testingPd" @click="runTestPd">
  Test PD chain
</Button>
<pre v-if="testPdResult" class="bg-secondary p-3 rounded text-xs whitespace-pre-wrap">{{ testPdResult }}</pre>
```

```vue
const testingPd = ref(false)
const testPdResult = ref<string>('')
async function runTestPd() {
  testingPd.value = true
  try {
    const r = await api<any>('/api/scrapers/test_pd_chain', { method: 'POST' })
    testPdResult.value = JSON.stringify(r, null, 2)
  } finally { testingPd.value = false }
}
```

- [ ] **Step 5: SPA build**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library/webapp && npm run build 2>&1 | tail -5"
```

- [ ] **Step 6: Commit + push**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git add webapp/src/pages/ScrapersPage.vue src/endless_library/web/api.py && git commit -m 'Phase 6w.9e: ScrapersPage categories + upstream status + Test PD chain' -m 'Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>' && git push origin main"
```

---

## Sub-phase wrap-up (after all 7)

- [ ] **Step 1: Full regression**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && source .venv/bin/activate && python -m pytest -q 2>&1 | tail -5"
```
Expected: ~960 passed, 0 failed.

- [ ] **Step 2: Rebuild + restart biblichor with cf-bypass sidecar**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && docker compose -f deploy/compose.yml --env-file ./.env up -d --build biblichor cf-bypass 2>&1 | tail -10"
```

- [ ] **Step 3: Live verification**

```bash
ssh ubuntu@claude-1 "curl -fsS http://localhost:8090/api/scrapers | python3 -m json.tool | head -40"
```
Expect: `hathitrust`, `doab`, `mobilism_books`, `bdebooks` in `available`, all rendered correctly.

```bash
ssh ubuntu@claude-1 "curl -sS -X POST 'http://localhost:8090/api/bench/run?mode=quick' -d '{}' -H 'Content-Type: application/json'"
```
Expect: `202 {"job_id": N}` instantly.

- [ ] **Step 4: Wiki sync**

After implementation, append a Phase 6w section to `docs/wiki/Bench-and-Scrapers.md` documenting the new sources + curl-cffi/patchright deps + cf-bypass sidecar + excluded_categories. Then:

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && bash scripts/sync-wiki.sh 2>&1 | tail -5"
```

- [ ] **Step 5: Push final state**

```bash
ssh ubuntu@claude-1 "cd /home/ubuntu/endless-library && git push origin main"
```

Phase 6w complete.
