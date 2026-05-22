# Phase 6w — Scraper Sweep: New Sources, Anti-Bot Foundation, Bench Async

**Date:** 2026-05-23
**Status:** Approved design — ready for implementation plan

## Goal

Land a coherent batch of scraper improvements that (a) widen book coverage in English + Bengali, (b) harden the existing chain against Cloudflare and upstream churn, and (c) fix the synchronous `/api/bench/run` that times out. Ships as seven independently mergeable sub-phases.

## Scope filter

User-imposed constraints from brainstorming:

- **Languages:** EN + Bengali only. No Russian, Arabic, Tamil, Nordic, French, multi-Indian-language sources.
- **No audiobooks.** Drop LibriVox, AudiobookBay, MyAnonaMouse, Mobilism audiobook subforum.
- **No Bangladeshi Muslim religious content.** Applied as a per-source `excluded_categories` denylist with default coverage of Islamic / Religious / Hadith / Quran / Prophet / Islamic Studies and Bengali equivalents.

## Sub-phase ship cadence

| # | Sub-phase | Hours |
|---|---|---|
| 6w.0 | Bench-as-async (job table + SSE progress + per-scraper timeout + circuit breaker) | 4 |
| 6w.1 | HTTP foundation: `curl-cffi` global swap + in-process Anubis PoW solver | 4 |
| 6w.2 | Anna's hardening: mirror rotation, parallel slot probe, sarperavci `cf-bypass` sidecar (default-on), `annas_cloakbrowser` rewrite | 4 |
| 6w.3 | PD/OA EN: `hathitrust` (ISBN lookup) + `doab` (REST search) | 3 |
| 6w.5 | Mobilism books (`mobilism_books`) — phpBB session + Mediafire resolver | 4 |
| 6w.6 | Bengali: `bdebooks` + `excluded_categories` content-filter abstraction (retroactively applied to `kindlebangla`) | 4 |
| 6w.9 | Hardening + UI: Patchright welib revive, zlib bench enablement, PD chain verification, Open Slum health, ScrapersPage categories | 4 |
| **Total** | | **~27h** |

Sub-phases removed during brainstorming: 6w.4 (Europeana/DPLA — user dropped API-key sources), 6w.7 (Tor/IPFS/Soulseek — operational cost), 6w.8 (Reddit PRAW + Telegram MTProto — discovery-source heaviness).

## Architecture

### Existing surface (extended, not replaced)

- `src/endless_library/scrapers/<name>.py` — each implements the `Scraper` protocol (`search() -> list[Candidate]`, `resolve_cdn() -> DownloadHandle`)
- `scrapers/registry.py` — registry + `enabled_order` / `pd_aware_order` / `chain_for_source`
- `domain/models.py` — `Candidate`, `SearchQuery`, `DownloadHandle`; gains a `categories: tuple[str, ...]` field in 6w.6
- `pipeline.py` — calls `chain_for_source` per book; gains a "recent_release" hint in 6w.5 for Mobilism promotion
- `bench.py` + `db/bench.py` — corpus + outcomes; gains async-job table in 6w.0

### New abstractions introduced

1. **`scrapers/http_client.py`** — `make_client(impersonate="chrome", proxies=None)` factory returning a `curl_cffi.requests.Session` with Anubis middleware installed. All scrapers migrate to this.
2. **`scrapers/anubis.py`** — pure-Python SHA-256 PoW solver; ~30 LOC. Middleware retries failed requests after solving.
3. **`bench_jobs` SQLite table** — async bench job state with progress + status; SSE-streamed events per outcome.
4. **`Candidate.categories`** field — populated by sources that expose category metadata; drives `excluded_categories` filter.
5. **Per-source `excluded_categories` config** — list in `config.yaml` under each source, with defaults reflecting the user's filter directive.

### Compose changes

- New default-on service `cf-bypass` (sarperavci/CloudflareBypassForScraping image) on the internal `biblichor` network at port 8000. Heavier baseline (~Chromium container) but no opt-in step.
- No other new compose services (Tor / slskd / IPFS deferred).

## Per-sub-phase design

### 6w.0 — Bench as async job

**Problem:** `POST /api/bench/run` runs all enabled scrapers × queries serially in one FastAPI request thread, with default 60s per-call timeouts. With upstream 502s, the call blocks for 5+ minutes; SPA/curl times out and surfaces as "HTTP 500".

**Design:**

New SQLite table:

```sql
CREATE TABLE bench_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    mode            TEXT NOT NULL,                       -- "quick" | "full" | "scraper:<name>"
    status          TEXT NOT NULL,                       -- "running" | "done" | "cancelled" | "failed"
    progress_done   INTEGER NOT NULL DEFAULT 0,
    progress_total  INTEGER NOT NULL,
    summary_json    TEXT                                 -- format_table output, populated on done
);
```

Endpoints:

| Method | Path | Behavior |
|---|---|---|
| POST | `/api/bench/run?mode=quick\|full` | Returns `202 {"job_id": N}`; spawns asyncio task |
| GET | `/api/bench/jobs/{id}` | Job row + outcomes-so-far |
| GET | `/api/bench/jobs` | Recent jobs list (limit 20) |
| GET | `/api/bench/jobs/{id}/stream` | SSE — one event per outcome + terminal event |
| POST | `/api/bench/jobs/{id}/cancel` | Sets `status=cancelled`; worker checks between queries |

**Per-query timeout cap:** each `scraper.search(query)` wrapped in `asyncio.wait_for(..., timeout=cfg.bench.per_query_timeout_sec)` (default 20s). Timeouts recorded as `success=false, note="timeout"`.

**Circuit breaker:** 3 consecutive failures (timeout or exception) from a single scraper in one bench run skips its remaining queries (`note="circuit-broken"`). Resets on next run.

**SPA changes (ScrapersPage):** bench buttons return job_id, progress bar fed by SSE (falls back to 2s polling on disconnect), Cancel button while running.

**Tests:**
- `test_bench_run_returns_202_with_job_id`
- `test_bench_job_progress_increments`
- `test_bench_job_circuit_breaker_skips_after_3_consecutive_fails`
- `test_bench_job_per_query_timeout_recorded_as_failure_not_exception`
- `test_bench_job_cancel_flag_stops_worker_between_queries`
- `test_bench_sse_emits_event_per_outcome`

### 6w.1 — HTTP foundation: curl-cffi + Anubis solver

**`curl-cffi` swap.** New module `scrapers/http_client.py`:

```python
from curl_cffi import requests as cffi_requests

def make_client(*, impersonate: str = "chrome", timeout: float = 30.0,
                proxies: dict | None = None) -> cffi_requests.Session:
    s = cffi_requests.Session(impersonate=impersonate, timeout=timeout)
    if proxies:
        s.proxies.update(proxies)
    _install_anubis_middleware(s)
    return s
```

`curl_cffi.requests.Session` is intentionally httpx-API-compatible. Migration is one-line per scraper. Order (lowest-risk first, fixture tests at each step):

1. `gutendex`, `standard_ebooks`, `oapen_doab`, `wikisource` (sanity batch; no CF)
2. `archive_curl`, `libgen_curl`, `kindlebangla_curl` (zero-CF; sanity batch)
3. `annas_curl`, `welib_curl` (CF-protected; expect higher success rate post-swap)
4. New scrapers (6w.3, 6w.5, 6w.6) use `make_client()` from day one

**`annas_cloakbrowser` outcome:** rewritten in 6w.2 to talk to the sarperavci sidecar; the original in-process DrissionPage path is removed. Name preserved to avoid registry churn.

**Anubis PoW solver.** New module `scrapers/anubis.py`:

```python
import hashlib

def solve_anubis(challenge: str, difficulty: int) -> int:
    """Find nonce N such that sha256(challenge + N) has >= `difficulty`
    leading zero bits. Returns N. Pure-Python; ~1-50ms typical."""
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

Wired as middleware in `http_client._install_anubis_middleware`. On any 200 HTML response matching Anubis signatures (`<meta name="anubis-challenge">` or `<title>Making sure you're not a bot...</title>`):

1. Parse challenge string + difficulty from inline JSON
2. Solve via `solve_anubis`
3. POST nonce to challenge endpoint (path from form action)
4. Capture JWT cookie
5. Retry original request with cookie attached
6. Cache cookie per host (TTL ~50 min)

**Tests:**
- `test_solve_anubis_finds_valid_nonce_at_difficulty_8`
- `test_solve_anubis_returns_correct_nonce_at_difficulty_16`
- `test_solve_anubis_handles_non_byte_aligned_difficulty`
- `test_anubis_middleware_only_triggers_on_anubis_signature`
- `test_anubis_middleware_retries_original_request_with_jwt`
- One per migrated scraper: fixture-based smoke test confirming candidates are still returned

### 6w.2 — Anna's hardening (mirror rotation, parallel slot, sarperavci sidecar)

**Mirror rotation.** Extend `scrapers/annas_domains.py` with:

```python
_MIRRORS = ("annas-archive.gl", "annas-archive.li", "annas-archive.pm", "annas-archive.in")
_COOL_DOWN_SEC = 5 * 60

def next_mirror(prefer_last_working: bool = True) -> str:
    """Returns next available (non-cooling) mirror. Pins to the most-
    recently-successful when prefer_last_working=True."""

def mark_cool(host: str) -> None: ...
def mark_success(host: str) -> None: ...
```

All `annas_*` scrapers (curl, flaresolverr, playwright, cloakbrowser-via-sidecar) consume `next_mirror()` instead of a hard-coded hostname.

**Parallel slot probing.** New helper `_resolve_slow_download_parallel(md5, max_slots=5)` in `annas_curl.py`. Hits all slot URLs concurrently with `curl-cffi.AsyncSession`; first slot to return a non-countdown direct link wins; the rest are cancelled.

**sarperavci `cf-bypass` sidecar.** Default-on compose service:

```yaml
  cf-bypass:
    image: sarperavci/cloudflarebypassforscraping:latest
    container_name: biblichor-cf-bypass
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 30s
    networks:
      - biblichor
```

New module `scrapers/cf_bypass_client.py`:

```python
def resolve(url: str, *, timeout: float = 90.0) -> str:
    """POST url to cf-bypass sidecar; returns resolved HTML."""
    base = os.environ.get("CF_BYPASS_URL", "http://cf-bypass:8000")
    r = httpx.post(f"{base}/cf-clearance-scraper", json={"url": url}, timeout=timeout)
    r.raise_for_status()
    return r.json()["html"]
```

`scrapers/annas_cloakbrowser.py` rewritten to use `cf_bypass_client.resolve()` instead of its prior in-process DrissionPage stack. Name + registry slot preserved.

**Resulting Anna's chain:**

```
annas_curl (curl-cffi, mirror-rotated)
  → annas_flaresolverr
  → annas_cloakbrowser (sarperavci sidecar)
  → annas_playwright   (deepest fallback)
```

**Tests:**
- `test_annas_domains_rotates_on_502`
- `test_annas_domains_cools_down_for_5_minutes_then_retries`
- `test_resolve_slow_download_parallel_returns_first_winner`
- `test_resolve_slow_download_parallel_raises_when_all_countdown`
- `test_cf_bypass_client_posts_url_and_returns_html`
- `test_annas_cloakbrowser_routes_through_sidecar`

### 6w.3 — PD/OA EN: HathiTrust + DOAB

**HathiTrust** — `scrapers/hathitrust.py`. **ISBN lookup only** (not full-text search; Hathifiles bulk ingestion deferred to a possible future phase).

```python
class HathiTrust:
    name = "hathitrust"
    category = "pd"

    def search(self, q: SearchQuery) -> list[Candidate]:
        if not q.isbn13:
            return []
        r = self.client.get(f"https://catalog.hathitrust.org/api/volumes/brief/json/isbn:{q.isbn13}")
        out = []
        for rec in r.json().get("records", {}).values():
            for item in rec.get("items", []):
                if item.get("rightsCode") in {"pd", "pdus"}:
                    htid = item["htid"]
                    out.append(Candidate(
                        title=rec["titles"][0],
                        url=f"https://babel.hathitrust.org/cgi/imgsrv/download/pdf?id={htid}",
                        format="pdf",
                        provider=self.name,
                    ))
        return out
```

**DOAB** — `scrapers/doab.py`. REST search at `/rest/search?query=<keywords>&expand=metadata`.

```python
class Doab:
    name = "doab"
    category = "pd"

    def search(self, q: SearchQuery) -> list[Candidate]:
        params = {"expand": "metadata", "query": q.title}
        if q.language:
            params["query"] += f" AND language:{q.language}"
        r = self.client.get("https://directory.doabooks.org/rest/search", params=params)
        out = []
        for it in r.json()[:20]:
            md = {kv["key"]: kv["value"] for kv in it.get("metadata", [])}
            url = md.get("oapen.relation.isPartOfBook") or md.get("dc.identifier.uri")
            if url:
                out.append(Candidate(
                    title=md.get("dc.title", q.title),
                    author=md.get("dc.creator", ""),
                    url=url,
                    format="pdf",
                    provider=self.name,
                ))
        return out
```

DOAB has ~90k books — broader than OAPEN's ~30k. Dedup happens upstream at the pipeline ranker (already de-dups by ISBN/title hash).

**Chain promotion.** Add to `_PD_PRIORITY` in `registry.py`:

```python
_PD_PRIORITY = (
    "standard_ebooks", "gutendex", "wikisource",
    "oapen_doab", "doab", "hathitrust",
)
```

**Tests:**
- `test_hathitrust_lookup_by_isbn_returns_pd_candidate`
- `test_hathitrust_filters_out_in_copyright_rights_codes`
- `test_doab_search_query_includes_language_filter`
- `test_doab_returns_pdf_candidates`

### 6w.5 — Mobilism books

**Shared session** — `scrapers/mobilism.py`:

```python
class MobilismSession:
    """Shared login + session-cookie cache. TTL ~24h; re-login on 401."""

    @classmethod
    def get(cls, svc) -> "MobilismSession":
        if cls._instance is None or cls._instance._expired():
            cls._instance = cls._build(svc)
        return cls._instance

    @classmethod
    def _build(cls, svc) -> "MobilismSession":
        creds = svc.get_secret_value("mobilism.username"), svc.get_secret_value("mobilism.password")
        if not all(creds):
            raise NotConfigured("mobilism credentials missing")
        s = make_client()
        r = s.post("https://forum.mobilism.org/ucp.php?mode=login",
                   data={"username": creds[0], "password": creds[1],
                         "login": "Login", "redirect": "./index.php", "autologin": "on"},
                   allow_redirects=True)
        if "ucp.php?mode=login" in r.url:
            raise AuthFailed("login redirect failed")
        return cls(session=s, established_at=time.time())
```

Credentials stored in encrypted secrets store via existing `BookOrbitService.set_secret_value` (Phase 6s.5 pattern).

**Books scraper** — `scrapers/mobilism_books.py`:

```python
class MobilismBooks:
    name = "mobilism_books"
    category = "general"
    FORUM_ID = 15

    def search(self, q: SearchQuery) -> list[Candidate]:
        sess = MobilismSession.get(self._svc).session
        r = sess.get("https://forum.mobilism.org/search.php",
                     params={"keywords": q.title, "fid[]": self.FORUM_ID, "sf": "titleonly"})
        threads = _parse_thread_list(r.text)
        out = []
        for tid, title, post_url in threads[:5]:
            resolved = self._resolve_post_links(sess, post_url)
            if resolved:
                out.append(Candidate(title=title, url=resolved, format="epub",
                                     provider=self.name))
        return out
```

**Link extraction:** prefer `mediafire.com` URLs (resolved via the new `scrapers/mediafire_helpers.py` ported from Greasyfork userscript 499381). Direct file URLs (`.epub`/`.pdf`/`.azw3`/`.mobi`) also accepted. MEGA / Rapidgator / 1fichier links are visible but skipped this phase (MEGA needs the official SDK; Rapidgator is paid).

**Chain promotion** for recent releases: `chain_for_source` gains a `is_recent_release` hint (true when `book.pub_year >= current_year - 1`, tunable via `cfg.scrapers.recent_release_window_years`). When true, `mobilism_books` is promoted to the front of the general chain, ahead of Anna's. Falls through normally if Mobilism returns 0 candidates.

**Settings UI:** new "Mobilism" card on Settings → Scrapers (sibling to existing Z-Library card) with username + password inputs + "Test login" button.

**Tests:**
- `test_mobilism_session_logs_in_and_caches_cookie`
- `test_mobilism_session_reauths_on_401`
- `test_mobilism_session_raises_when_creds_missing`
- `test_mobilism_books_filters_to_books_subforum`
- `test_mobilism_resolve_post_prefers_mediafire`
- `test_mobilism_resolve_post_skips_mega_and_rapidgator`
- `test_mediafire_resolver_extracts_dynamic_url`
- `test_mediafire_resolver_returns_none_on_no_match`
- `test_settings_mobilism_test_creds_returns_ok_or_error`

### 6w.6 — Bengali: BDeBooks + content-filter abstraction

**`Candidate.categories`** — new field in `domain/models.py`:

```python
@dataclass(frozen=True, slots=True)
class Candidate:
    # ... existing fields ...
    categories: tuple[str, ...] = ()
```

Existing scrapers default to `categories=()`; only sources that surface category metadata populate it.

**Content-filter abstraction** — in each Bengali scraper's `search()`:

```python
def search(self, q: SearchQuery) -> list[Candidate]:
    cands = self._search_upstream(q)
    excluded = set(self._cfg.excluded_categories or ())
    return [c for c in cands if not (set(c.categories) & excluded)]
```

`excluded_categories` per source in `config.yaml`. Default lists (matching user's filter directive):

```yaml
scrapers:
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

User editable via Settings → Scrapers → per-source card with a comma-separated input bound to this list.

**BDeBooks** — `scrapers/bdebooks.py`:

- Live search: `GET bdebooks.com/?s=<query>` returns WordPress search results
- Parse `.post` blocks for title, author, category links, book detail URL
- Open book detail page → extract direct PDF URL
- `category = "general"`, language pin for Bengali queries via `_NON_LATIN_PRIORITY` extension
- Format: PDF
- No auth

**`_NON_LATIN_PRIORITY` extension** in `registry.py`:

```python
_NON_LATIN_PRIORITY = ("kindlebangla_curl", "bdebooks")
```

`enabled_order_for_query` already promotes this tuple to the front when the query has non-Latin characters.

**KindleBangla retroactive filter:** existing scraper extended to populate `Candidate.categories` from the kindlebangla source's category URL, and to apply the same `excluded_categories` filter.

**Tests:**
- `test_candidate_model_has_categories_field_default_empty_tuple`
- `test_bdebooks_search_extracts_titles_and_categories`
- `test_bdebooks_search_excludes_islamic_when_in_denylist`
- `test_bdebooks_search_includes_book_when_no_categories_match_denylist`
- `test_kindlebangla_with_excluded_categories_filters_retroactively`
- `test_non_latin_priority_promotes_bdebooks`
- `test_settings_scraper_excluded_categories_can_be_edited_via_api`

### 6w.9 — Hardening + UI

**Patchright welib revive.** One-line dep swap in `scrapers/welib_playwright.py`:

```python
from patchright.sync_api import sync_playwright  # was playwright.sync_api
```

Re-enable in default config. Verify via existing welib_playwright test suite.

**zlib_singlelogin bench enablement.** Add to `bench/queries.yaml`:

```yaml
corpus_tags:
  # ... existing ...
  zlib_singlelogin: [en, modern]
```

New guard in `run_bench`: if a specialised scraper's required creds are unset (raises `NotConfigured` at build time), record outcome as `success=false, note="creds-missing"` instead of letting the exception propagate.

**PD chain promotion verification.** No code changes expected — add an integration test:

```python
def test_pd_chain_promotes_pd_scrapers_for_pre_1928_books():
    """Mock a 1850-pub-year book through the pipeline; assert PD
    scrapers are queried before annas/libgen."""
```

UI affordance:
- ScrapersPage shows "fires-when: PD-only" badge for PD-category scrapers
- New "Test PD chain" button → `POST /api/scrapers/test_pd_chain` runs "Pride and Prejudice" through the full pipeline and reports which scraper resolved it

**Open Slum health integration.** New module `scrapers/open_slum.py`:

```python
class OpenSlumMonitor:
    URL = "https://open-slum.org/status.json"   # confirm endpoint shape on impl
    POLL_INTERVAL_SEC = 600

    def get(self, site: str) -> dict | None:
        if time.time() - self._last_poll > self.POLL_INTERVAL_SEC:
            self._refresh()
        return self._cache.get(site)
```

Wired into:
- `/healthz` — adds `external_sources: {annas: {up: bool, ...}, libgen: {...}}`
- `/api/scrapers` — each row includes `upstream_status`
- ScrapersPage UI — small green/red dot next to each scraper

**ScrapersPage categories UI.** Group scrapers by category in collapsible `<details>` sections:

- "general" (Anna's variants, libgen, welib, archive_curl, kindlebangla_curl, bdebooks, mobilism_books, zlib_singlelogin)
- "pd" (gutendex, standard_ebooks, oapen_doab, wikisource, doab, hathitrust)
- DnD reorder still works within a category
- Section header shows `<category>: N scrapers, M in chain`
- Inline `corpus: <tags>` badge from Phase 6v.4 clarifies why PD scrapers only fire some of the time

**Tests:**
- `test_welib_playwright_uses_patchright_not_vanilla`
- `test_bench_skips_scraper_when_creds_missing_without_exception`
- `test_pd_chain_promotes_pd_scrapers_for_pre_1928_books`
- `test_pd_chain_skips_pd_scrapers_for_modern_books`
- `test_open_slum_monitor_caches_within_interval`
- `test_open_slum_monitor_refresh_after_interval_expiry`
- `test_open_slum_monitor_handles_endpoint_unreachable_gracefully`
- `test_healthz_includes_external_sources_when_monitor_running`
- `test_scrapers_page_groups_by_category`

## Cross-cutting testing strategy

- **No live upstream calls in tests.** Every scraper test mocks the HTTP client (existing `respx` pattern already in use). Anubis tests are pure-Python (deterministic).
- **Fixture pinning per migrated scraper.** Each curl-cffi-migrated scraper gets a fixture of a recorded successful response; the smoke test asserts candidates parse identically before vs after migration.
- **Bench async tests use synthetic scraper stubs.** Don't reach Anna's; tests run in &lt; 5s.
- **Live verification at the end of each sub-phase.** After tests pass, the user runs `POST /api/bench/run?mode=quick` and inspects per-scraper outcomes via the new job UI.

## Risks and open questions

1. **`cf-bypass` upstream image stability.** The sarperavci image is community-maintained. Pin to a specific tag; document in deploy/env.example. Fall back to the old `annas_cloakbrowser` git history if the sidecar disappears.
2. **Mobilism CAPTCHA on login.** If Mobilism adds a CAPTCHA, the curl-cffi-based session login will break. Mitigation: route Mobilism login through the `cf-bypass` sidecar (one extra ~5s setup on each cookie refresh). Out of scope for this phase; documented as a follow-up.
3. **HathiTrust ISBN coverage.** Limiting to ISBN lookups means recently-published books (which Anna's covers anyway) get nothing from HathiTrust. The win is for older books with ISBNs that ARE in HathiTrust's PD pool. If this turns out too narrow, Hathifiles full ingestion is a clear follow-up.
4. **DOAB / OAPEN dedup.** Both indices likely overlap on ~30% of titles. The existing pipeline ranker dedups by ISBN/title-hash, so duplicates are absorbed. No action this phase.
5. **Patchright maintenance.** Project is active in 2026 but smaller than Playwright. Pin to a major version; pre-check on each biblichor image build.
6. **Open Slum endpoint format.** Need to confirm `status.json` shape on implementation. Fallback: scrape the HTML status page.

## Out of scope (rejected / deferred)

- All audiobook sources (LibriVox, AudiobookBay, MyAnonaMouse, Mobilism audiobook subforum)
- Non-EN/Bn sources (Flibusta Russian, FreeTamilEbooks, Hindawi Arabic, Project Runeberg Nordic, EbooksGratuits French, NDLI multi-Indian-language)
- PG Australia + Faded Page (IndexedSource overhead not worth it for ~18k books)
- Europeana + DPLA (API-key friction for uncertain coverage)
- Mobilism magazines subforum
- IPFS gateway rotation, Tor onion rung, Soulseek/slskd
- Reddit PRAW + Telegram MTProto (discovery sources, not scrapers)
- Hathifiles bulk full-text search ingestion (defer until/unless ISBN-only proves too narrow)
- MEGA / Rapidgator / 1fichier link resolution for Mobilism

## Dependencies added

- `curl-cffi` (Python) — TLS-fingerprint-matching HTTP client
- `patchright` (Python) — patched Playwright clone for the welib path
- `praw` — would have been added if Reddit source was in scope (cut during brainstorming)
- `telethon` — would have been added if Telegram source was in scope (cut during brainstorming)

Image-level:

- `sarperavci/cloudflarebypassforscraping` (Docker) — anti-bot sidecar

## Acceptance criteria

- All 7 sub-phases land green with tests passing (target: ~895 → ~960 passed after this phase)
- `/api/bench/run?mode=quick` returns within 200ms with a job_id (no more sync block)
- The Phase 6v live verification (BookOrbit doctor green, all containers healthy) continues to pass after each sub-phase
- ScrapersPage shows scrapers grouped by category with the new badges (in chain / never tested / corpus / upstream status / fires-when)
- `cf-bypass` sidecar runs healthy in production; `annas_cloakbrowser` resolves through it on a manual test

## Wiki updates

After implementation, update `docs/wiki/Bench-and-Scrapers.md`:

- New sources catalog (BDeBooks, DOAB, HathiTrust, Mobilism, sarperavci sidecar)
- `excluded_categories` configuration reference
- `cf-bypass` profile / sidecar operational notes
- `curl-cffi` and `patchright` deps + migration order
- New ScrapersPage UI map

Sync via `scripts/sync-wiki.sh`.
