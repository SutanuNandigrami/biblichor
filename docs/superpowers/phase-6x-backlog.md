# Phase 6x Backlog — Ultrareview Carry-Over

After Phase 6w shipped (commits up to merge point), the cross-cutting ultrareview surfaced 7 Critical + 14 Important + 15 Minor findings. All 7 Critical and 9 of the 14 Important were fixed before the phase-6w → main merge. The remainder live here as a documented backlog.

## Important (defer to Phase 6x)

### I6 — `_probe_slow_servers_async` uses `asyncio.run()` from sync code
**Location:** `src/endless_library/scrapers/annas_curl.py:_probe_slow_servers_async`
**What:** Safe today because all current call paths invoke it from `asyncio.to_thread`. If a future code path runs it from inside an existing event loop, raises `RuntimeError`.
**Fix sketch:** Replace `asyncio.run` with a thread-pool race that doesn't require a fresh event loop.

### I8 — `HathiTrust` / `DOAB` clients have lifecycle leak
**Location:** `src/endless_library/scrapers/hathitrust.py`, `doab.py`
**What:** `self.client = make_client(...)` created in `__init__`; never closed. With `registry.build` called per search, fd accumulation per scrape over months.
**Fix sketch:** Move client creation inside `search()` with `with`-statement.

### I9 — SSE polling pressure
**Location:** `src/endless_library/web/api.py` `_events()` (bench job stream)
**What:** Each SSE-watching tab polls the DB every 0.5s. 5 tabs × 3 jobs = 30 connect/sec.
**Fix sketch:** Single broadcaster task with per-client queues, OR raise poll interval to 1.5s.

### I10 — `_reset_session` exposed as test hook
**Location:** `src/endless_library/scrapers/mobilism.py`
**What:** Partially resolved by I1's `try_login`. The `_reset_session` is still imported in tests; document its test-only scope.
**Fix sketch:** Rename to `_reset_session_for_tests`.

### I13 — Non-atomic credential rotation
**Location:** `src/endless_library/web/api.py` `mobilism_store_creds`
**What:** Two `set_secret_value` calls (username + password). If second fails, store has new username + old password.
**Fix sketch:** Add `BookOrbitService.set_secret_values(dict)` that wraps in a sqlite transaction.

## Minor (defer or close as no-action)

### M1 — `True and IPv6Network(...)` in url_safety.py
Cosmetic. Replace with literal tuple of 4 networks.

### M2 — `BulkDelete.created_after/before` lexicographic comparison
Could mis-sort `"2024-1-1"` non-canonical ISO strings. Add `datetime.fromisoformat()` parse + re-serialize.

### M3 — `OpenSlumMonitor._fetch_remote` has no schema validation
Reduce to known keys; log unknown.

### M4 — `parse_domains_from_html` uses raw-byte regex
Switch to lxml; ~10 lines.

### M5 — `getattr(r, "status_code", 0)` masks no-response as 0
Defensive code that's never reached. Plain attribute access is fine.

### M6 — `mobilism_books` hardcodes forum_id=15
If Mobilism reorgs the subforum, scraper silently 0s. Add a "configuration-drift" sanity check on a known-good search term.

### M7 — `_search_with_strategies` / `_resolve_and_download` dup `is_pd/is_recent` compute
Extract `_book_context(book, cfg) -> (is_pd, is_recent_release, source)`.

### M9 — `_swap_compose_image` regex assumes contiguous yaml block
Today's compose works. If we add yaml anchors, switch to `ruamel.yaml`.

### M10 — `_check_member_safe` refuses Windows `\` separator unconditionally
Normalize `\` to `/` before checks; some legacy CBR uses backslashes.

### M11 — OpenSlumMonitor refresh failures logged at debug only
Bump to WARNING after N consecutive failures.

### M12 — `_reset_state()` in annas_domains should be `_reset_state_for_tests`
Pure rename for clarity.

### M13 — `cf_bypass_client.resolve` no retry/backoff
Single connection error = empty result. Add one retry with 5s backoff.

### M14 — `bench_jobs` uses AUTOINCREMENT vs other tables don't
Cosmetic inconsistency. AUTOINCREMENT guarantees monotonic ids (nice for URL-addressable jobs); justified or drop.

### M15 — Inconsistent User-Agent across scrapers
Anna's = Chrome via curl-cffi, BDeBooks = "biblichor/0.1", HathiTrust = default. Define one project-default in `make_client`.

## Closed (already addressed during 6w cleanup)

- I1 MobilismSession test-creds race — fixed via `try_login` classmethod
- I2 Anubis middleware only wraps GET — fixed (all 5 verbs now wrapped)
- I3 _ANUBIS_COOKIE_CACHE no lock — fixed (threading.Lock added)
- I4 annas_domains no lock — fixed (threading.Lock added)
- I5 OpenSlumMonitor no lock — fixed (lock + claim-before-fetch)
- I7 BDeBooks N+1 — fixed (capped at 3 detail fetches)
- I11 Dockerfile rar packages — already present from Phase 6u.7
- I12 TOCTOU on apply — fixed (asyncio.Lock + 409)
- I14 archive_safety bsdtar path traversal — fixed (post-extract verification)
- M8 chain_for_source kindlebangla enabled check — folded into C7

All 7 Critical and the bench_jobs cancel race (C2) shipped in Phase 6w cleanup commits 91556d3..aef1b10.
