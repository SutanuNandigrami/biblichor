# biblichor

Self-hosted automation that watches your reading lists, finds the books on Anna's Archive (with Welib and LibGen fallbacks), converts them where needed, and emails them to your Kindle — with a Vue 3 dashboard, an embedded Calibre-Web library, and a scheduler you control from the browser.

> The Python package is named `endless_library` internally; the project / repo is `biblichor`. Both names refer to the same thing.

---

## What it does

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  Goodreads RSS   │    │   Hardcover      │    │  Manual / Listopia│
│  Listopia/Series │    │   GraphQL        │    │  / Series source  │
└────────┬─────────┘    └─────────┬────────┘    └─────────┬────────┘
         │                        │                       │
         └────────────┬───────────┴───────────┬───────────┘
                     ▼                        ▼
              ┌──────────────────────────────────────┐
              │   Source poll jobs (one per source)  │
              │   APScheduler, intervals per source  │
              └────────────────┬─────────────────────┘
                               ▼
                      ┌────────────────────┐
                      │   books queue (SQLite WAL)
                      │   states: new → queued → searching →
                      │   needs_review | downloaded → converted →
                      │   sent | failed | skipped
                      └────────┬───────────┘
                               ▼
      ┌────────────────────────────────────────────────────────────┐
      │ scraper strategies, bench-ranked + fallback chain          │
      │                                                            │
      │  Anna's Archive:                                           │
      │   1. annas_curl           — curl-cffi (Chrome120 TLS impersonation)
      │   2. annas_flaresolverr   — Cloudflare challenge solver
      │   3. annas_cloakbrowser   — stealth headless Chromium     │
      │   4. annas_playwright     — vanilla Playwright (last resort)
      │                                                            │
      │  Welib (CF Turnstile-gated):                               │
      │   5. welib_curl + IPFS gateway iteration (40+ gateways)    │
      │   6. welib_playwright (auth-cookie injection optional)     │
      │                                                            │
      │  LibGen mirrors (libgen.li/.is/.rs/.st):                   │
      │   7. libgen_curl + IPFS fallback                           │
      └────────────────────────────┬───────────────────────────────┘
                                   ▼
                ┌──────────────────────────────────────┐
                │ score candidates                     │
                │  ISBN match (35) + title rapidfuzz   │
                │  + author rapidfuzz + format bonus   │
                │  + language bonus + filesize sanity  │
                │  − scan penalty − derivative skip    │
                └──────────────┬───────────────────────┘
                               ▼
                ┌──────────────────────────────────────┐
                │ auto-pick if score ≥ threshold       │
                │ OR (score ≥ threshold + bonus and no │
                │  gap requirement) → needs_review     │
                └──────────────┬───────────────────────┘
                               ▼
       ┌─────────────────────────────────────────────────────┐
       │ download (streaming + MD5 verify + .part resume)    │
       │      → convert (Calibre `ebook-convert` if needed)  │
       │      → enrich metadata (`ebook-meta` author/series/ │
       │         tags/ISBN) — Kindle uses these for collections
       │      → add to Calibre library (`calibredb add`)     │
       │      → email to Kindle (aiosmtplib + STARTTLS)      │
       └─────────────────────────────────────────────────────┘
                               ▼
                         ┌───────────┐
                         │  Kindle   │
                         └───────────┘

      ┌──────────────────────────────────────────────────────┐
      │  Dashboard (FastAPI + Vue 3 SPA, Tailscale-friendly) │
      │  /queue       Books queue + bulk delete + retries    │
      │  /sources     Add/edit Goodreads (shelf, Listopia,   │
      │               series), Hardcover, manual             │
      │  /scrapers    Toggle strategies + see bench history  │
      │  /mirrors     Curated mirror health table            │
      │  /scoring     Live-edit scoring weights              │
      │  /schedule    Pause/resume/run/reschedule any job    │
      │  /settings    SMTP, Pushover, Kindle, polling tuning │
      │  /logs        Recent events stream (WebSocket)       │
      │  /lib         Embedded Calibre-Web (same-origin via  │
      │               reverse proxy at /library/*)           │
      └──────────────────────────────────────────────────────┘
```

## Features

| Capability | Details |
|---|---|
| **Reading-list sources** | Goodreads RSS shelves, Goodreads Listopia, Goodreads series (main-series filter), Hardcover GraphQL, manual entries |
| **Anna's Archive client** | curl-cffi Chrome120 impersonation, FlareSolverr session warm-up, fast-fail on 403/429/503, CDN URL resolution from `/slow_download/` |
| **Anti-Cloudflare** | FlareSolverr session lifecycle, optional stealth headless Chromium via CloakBrowser, Playwright fallback |
| **Welib fallback** | `/auto_download`, `/slow_download`, IPFS gateway iteration over 40+ gateways with HEAD-verify |
| **LibGen fallback** | Column-positional table parsing across libgen.li/.is/.rs/.st with IPFS via `/ads.php?md5=` |
| **Auto mirror discovery** | 6-hour background job scrapes Anna's Wikipedia infobox; new mirrors flow into config automatically without losing the hardcoded baseline |
| **Format handling** | EPUB native, AZW3/MOBI/PDF converted via Calibre `ebook-convert`, sized-checked against `attachment_max_mb` before SMTP |
| **Metadata enrichment** | Calibre `ebook-meta` writes author, series, tags, ISBN into the file before Kindle send so it shows up in collections |
| **Calibre integration** | Files auto-added via `calibredb add`; full Calibre-Web library embedded in the dashboard at `/lib` (same-origin reverse proxy, iframe-safe) |
| **Kindle email send** | `aiosmtplib` with STARTTLS, app-password support (Gmail), per-attachment size guard |
| **Scoring** | ISBN match (35) + title rapidfuzz + author rapidfuzz + format bonus + language bonus + filesize sanity + scan penalty + derivative-content (summary/conversation-starters/study-guide) hard-skip |
| **Resume** | Stage timestamps (`downloaded_at`, `converted_at`, `sent_at`) — resuming picks up at the highest completed stage |
| **Scheduler** | APScheduler inside the FastAPI process: per-source poll jobs, queue process, retry-failed, daily summary, mirrors refresh. Pause/run-now/reschedule from the browser, persisted to config.yaml + DB |
| **Bench** | Curated query set in `bench/queries.yaml`; CLI subcommand runs each strategy against each query, ranks by latency + success rate |
| **Notifications** | Pushover per-event (book sent / needs review / failure) and daily summary |
| **Storage** | SQLite WAL; 7 tables (books, candidates, events, source_accounts, bench_runs, mirrors, sources); idempotent ALTER migrations |
| **Dashboard** | Vue 3 + Tailwind + shadcn-vue + Pinia + Vue Router + Lucide icons + WebSocket event stream |
| **Deployment** | Native via systemd unit + uvicorn; FlareSolverr and Calibre-Web in Docker via compose |

## Prerequisites

| Component | Why | Notes |
|---|---|---|
| **Python 3.12** | Backend runtime | Tested on 3.12 only |
| **Node.js 20+** | Build the Vue 3 SPA | Built once at install time; not needed at runtime |
| **Calibre** | `ebook-convert`, `ebook-meta`, `calibredb` | Install the host package, not the Docker mod (faster restarts) |
| **Docker + docker compose** | FlareSolverr and Calibre-Web | Optional but recommended |
| **An SMTP account** | Send to Kindle | Gmail with an app password is the easy path |
| **Amazon Kindle email setup** | Whitelist sender, capture `@kindle.com` address | <https://www.amazon.com/sendtokindle> |
| **Optional: Tailscale** | Private dashboard exposure | Bind the dashboard to your Tailscale IP via `TAILSCALE_IP=` |
| **Optional: Pushover** | Push notifications | Free user key + create a free app token |

## Setup from scratch

```bash
# 1. Clone
git clone https://github.com/SutanuNandigrami/biblichor.git
cd biblichor

# 2. Create the Python venv and install
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

# 3. Install Calibre on the host (Ubuntu/Debian shown — see calibre-ebook.com
#    for other platforms)
sudo apt-get install -y calibre

# 4. Build the SPA (one-shot; rebuild only if you change webapp/src)
cd webapp
npm install
npm run build
cd ..

# 5. Copy and edit the config files
cp config/config.yaml.example config/config.yaml
cp .env.example config/.env

#    Then edit config/.env to set SMTP/Kindle/Pushover/Hardcover values:
#      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
#      KINDLE_EMAIL (the @kindle.com address)
#      PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN  (optional)
#      HARDCOVER_TOKEN                          (only if you use Hardcover)
#      TAILSCALE_IP                             (optional dashboard bind)
#
#    And config/config.yaml mostly works as-is — the only thing you usually
#    need to change is general.books_dir (where downloaded books are stored)
#    and general.timezone.

# 6. Whitelist your SMTP sender in your Amazon "Send to Kindle" approved list:
#    https://www.amazon.com/hz/mycd/myx#/home/settings/payment
#    → Personal Document Settings → Approved Personal Document E-mail List
#    Add the email you set as SMTP_USER.

# 7. Start FlareSolverr + Calibre-Web via Docker (optional but recommended)
docker compose up -d flaresolverr calibre-web

#    The first Calibre-Web start needs a tiny bit of UI setup. Open it in a
#    browser at http://localhost:8083 (it's bound to 127.0.0.1 only — the
#    biblichor dashboard reverse-proxies it at /library/* on its own port),
#    log in with admin/admin123, set the library path to /books, and
#    change the default password.

# 8. Smoke test the server in the foreground
. .venv/bin/activate
./scripts/run-native.sh
#    Visit http://localhost:8090 (or http://<tailscale-ip>:8090).
#    Add a source via the Sources page, click Run Now in the header.
#    Watch the queue work in /queue.
#    Press Ctrl-C when you're convinced it's working.

# 9. Install as a systemd unit so it starts on boot
sudo cp scripts/endless-library.service.template /etc/systemd/system/biblichor.service
sudo sed -i "s|__USER__|$USER|; s|__PROJECT_ROOT__|$PWD|" /etc/systemd/system/biblichor.service
sudo systemctl daemon-reload
sudo systemctl enable --now biblichor
sudo systemctl status biblichor
```

## First-run walkthrough

1. **Sources page** → Add source.
   - **Goodreads shelf**: identifier `YOUR_USER_ID:to-read` (your user ID is the number in any of your bookshelf URLs).
   - **Goodreads Listopia**: paste a list URL like `https://www.goodreads.com/list/show/112351.Name`.
   - **Goodreads Series**: paste a series URL like `https://www.goodreads.com/series/40395-mistborn`.
   - **Hardcover**: identifier `me` + paste your Bearer token from <https://hardcover.app/account/api>.
2. **Settings page** → confirm SMTP credentials show as set (the page never echoes the password). Click "Send test" to your Kindle if you want a smoke test.
3. **Schedule page** → see the live job table. Click Run-now on `poll:<id>` and `process` to kick off immediately.
4. **Queue page** → watch books transition. Books that auto-pick a candidate sail through. Ones that don't land in `needs_review` — click the book to see candidates and pick manually.
5. **Library page** (`/lib`) → Calibre-Web embedded; books arrive automatically after they're sent.

## Configuration reference

The two settings files do different things:

### `config/.env`
Secret credentials only. Never committed. Read by `load_config()` at startup; `save_config()` writes secrets back here, never into `config.yaml`.

### `config/config.yaml`
Operational settings. Live-editable from the Settings page (the page writes back to this file). Highlights:

```yaml
general:
  poll_interval_minutes: 60       # legacy default for new sources
  process_interval_minutes: 10    # how often to drain the queue
  retry_interval_hours: 6         # retry pass over failed/pending
  mirror_refresh_hours: 6         # Wikipedia → annas_mirrors refresh
  daily_summary_hour_utc: 14      # Pushover daily tally
  max_attempts: 5
  books_dir: ./data/books         # where files land (override for an external drive)
  auto_pick_threshold: 70         # min score to auto-pick a candidate
  auto_pick_gap: 10               # if next-best is within this gap, send to needs_review
  min_score_for_failure: 40       # below this → fail outright
scrapers:
  order: [annas_curl, annas_flaresolverr, annas_cloakbrowser,
          welib_curl, welib_playwright, libgen_curl]
  enabled: { annas_curl: true, ... }
  format_priority: [epub, azw3, mobi, pdf]
  annas_mirrors: [https://annas-archive.gl, https://annas-archive.pk, https://annas-archive.gd]
scoring:
  isbn_match: 35
  title_weight: 25
  author_weight: 15
  format_bonus: { epub: 10, azw3: 9, mobi: 8, pdf: 5 }
```

## Running the bench

The bench answers "given my current scrapers, how do they rank for representative queries?" — used after enabling/disabling strategies, after Anna's changes, or before making a config change with confidence.

```bash
python -m endless_library bench --quick
# or full set
python -m endless_library bench
```

Results are stored in the `bench_runs` SQLite table and rendered as a table.

## CLI

```bash
python -m endless_library run        # start the full app (the systemd unit does this)
python -m endless_library run-once   # one poll + process pass, then exit
python -m endless_library status     # print queue counts by status
python -m endless_library send <file.epub>  # send one file to Kindle, bypass pipeline
python -m endless_library bench [--quick]
```

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `/api/...` returns 200 but pages 500 | SPA not built | `cd webapp && npm install && npm run build` |
| Dashboard returns 404 for all routes | uvicorn pointed at wrong dir | Check `WorkingDirectory` in the systemd unit |
| Books stuck in `needs_review` | Auto-pick gap too tight or scoring strict | Lower `auto_pick_gap` or raise candidate scores in /scoring |
| `403` on Anna's | Cloudflare challenge active for your IP | Confirm FlareSolverr is up (`docker compose ps`); check `flaresolverr_url` in config |
| Kindle send fails with `(0, b'')` | Gmail rejected app password | Strip any spaces from the password — biblichor does this automatically but double-check; regenerate the app password |
| Kindle send returns 250 OK but nothing arrives | Sender not whitelisted | Add SMTP_USER to Approved Personal Document E-mail List on Amazon |
| Calibre-Web 502 at `/library/*` | Calibre-Web container not running | `docker compose up -d calibre-web` |
| Calibre-Web thumbnails missing | Old SPA build | Rebuild: `cd webapp && npm run build` then restart |
| Scheduler jobs don't fire | systemd unit was running an old build | `sudo systemctl restart biblichor` — the scheduler starts inside FastAPI's lifespan |

## Security

### Archive hygiene + AV scanning

Some sources (notably `kindlebangla.com` for ~30% of its catalog) deliver the ebook inside a RAR or ZIP wrapper rather than as a bare `.epub`. We unpack these, but only after passing every member through a strict hygiene gate:

| Check | What it blocks |
|---|---|
| Magic byte detection | Files claiming to be archives that aren't (RAR or ZIP only) |
| Compressed size cap (default 200 MB) | Bandwidth/disk hoses |
| Extension whitelist | `.epub`/`.azw3`/`.mobi`/`.pdf`/`.jpg`/`.opf` and a few siblings only — everything else (`.exe`, `.sh`, `.lnk`, `.so`, ...) is fatal |
| Path traversal | Members containing `..` or starting with `/` are fatal |
| Nested archives | Refused outright (no RAR-in-RAR, ZIP-in-ZIP) |
| Zip-bomb protection | Total uncompressed size capped (default 500 MB) |
| Single ebook contract | Must contain exactly one of `.epub`/`.azw3`/`.mobi`/`.pdf`; multiple ebooks or zero is fatal |

These rules are **always on**; nothing in `config.yaml` disables them. The pipeline marks the book `failed` with the reason recorded in events.

### Optional ClamAV scan

After hygiene passes, the unpacked ebook is handed to `clamscan` if it's installed. By default ClamAV is treated as optional — its absence is a loud warning, not a failure. Toggle `security.require_clamav: true` in `config/config.yaml` to make it mandatory.

```bash
# Install
sudo apt install clamav clamav-daemon
sudo freshclam                # download signature DB (first time only)
sudo systemctl enable --now clamav-freshclam   # daily auto-update

# Then in config/config.yaml:
security:
  require_clamav: true
```

| Pipeline behavior | `require_clamav=false` | `require_clamav=true` |
|---|---|---|
| ClamAV not installed | ⚠ Warn, allow after hygiene pass | ✗ Fail every archive |
| ClamAV reports clean | ✓ Allow | ✓ Allow |
| ClamAV reports infected | ✗ Fail | ✗ Fail |
| ClamAV errors / times out | ✗ Fail | ✗ Fail |

Bare `.epub`/`.pdf` downloads (the common case) are also scanned by ClamAV when installed — the hygiene checks only apply to archives.

### Settings to tune

```yaml
security:
  require_clamav: false        # flip to true after installing clamav
  max_archive_size_mb: 200     # cap on the compressed wrapper
  max_extracted_size_mb: 500   # cap on total uncompressed size (zip-bomb guard)
  max_members: 50              # cap on number of entries in an archive
```

## Operational notes

- **Data layout**: everything goes under `data/`:
  - `data/library.db` — SQLite, WAL mode
  - `data/books/` — downloaded files
  - `data/calibre-library/` — Calibre library (Calibre-Web reads from here)
  - `data/cookies/` — per-domain FlareSolverr cookies
  - `data/logs/` — operation logs
  - `data/wiki_annas_domains.json` — refresh cache
- **Secrets never go into git**. `.gitignore` excludes `data/`, `*.db`, `config/config.yaml`, `config/.env`, `.env`.
- **Dashboard binding** defaults to `0.0.0.0`. Set `TAILSCALE_IP=100.x.y.z` in the systemd Environment line to bind to a single Tailscale IP and keep the dashboard private.
- **Restart cost**: ~3s. The systemd unit has `Restart=on-failure` + 5s back-off.

## Stack

- **Backend**: Python 3.12, FastAPI, Pydantic v2, APScheduler, SQLite WAL, aiosmtplib, httpx, curl-cffi, BeautifulSoup, rapidfuzz
- **Anti-bot**: FlareSolverr, optional CloakBrowser stealth Chromium, Playwright (last-resort)
- **Frontend**: Vue 3 + Vite, TypeScript, Tailwind CSS, shadcn-vue, Pinia, Vue Router, Lucide icons
- **Conversion**: Calibre (`ebook-convert`, `ebook-meta`, `calibredb`)
- **Library UI**: Calibre-Web in a container, embedded via reverse proxy

## Acknowledgements

- [Anna's Archive](https://annas-archive.org) — without which none of this exists
- [zelestcarlyone/stacks](https://github.com/zelestcarlyone/stacks) — the Wikipedia mirror auto-refresh idea (`utils/domainupdater.py`) is adapted from here
- [bbrown430/endless-library](https://github.com/bbrown430/endless-library) and the [Anna's Archive userscript](https://greasyfork.org/) for download-strategy lore
- [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr), [Calibre-Web](https://github.com/janeczku/calibre-web), [Calibre](https://calibre-ebook.com/), [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)

## License

MIT — see `LICENSE` if present, otherwise treat as MIT (do whatever you want, but no warranty).
