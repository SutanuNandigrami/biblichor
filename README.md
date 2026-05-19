# biblichor

[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Vue 3](https://img.shields.io/badge/vue-3-42b883.svg)](https://vuejs.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-async-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/tests-748%20passing-brightgreen.svg)]()
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue.svg)](LICENSE)

Self-hosted automation that watches your reading lists, finds the
books on Anna's Archive (with Welib, LibGen, archive.org, and
KindleBangla fallbacks), converts them, and emails them to your
Kindle — with a Vue 3 dashboard, an integrated BookOrbit library
(reader + Kobo sync + OPDS), and a scheduler you control from the
browser.

![biblichor dashboard](docs/screenshots/dashboard.png)

---

## What you get

- **Acquisition pipeline** — Goodreads / Hardcover / manual sources →
  ranked scraper chain (curl-cffi, FlareSolverr, CloakBrowser,
  Playwright) → score / auto-pick → download → convert → email to
  Kindle.
- **Integrated reader (BookOrbit)** — every book biblichor delivers
  also lands in BookOrbit's library: in-browser EPUB/PDF reader,
  Kobo auto-push, KOReader two-way sync, OPDS catalog, reading
  statistics.
- **Dashboard-driven** — queue, sources, scrapers, mirrors, scoring,
  schedule, settings, logs, library: all from the browser. The CLI
  exists for headless installs but the SPA is the primary surface.
- **First-run BookOrbit setup wizard** — admin account, library
  creation, encrypted credential storage, scan + doctor buttons —
  all inside biblichor's `/library` page. No CLI required after the
  initial `bootstrap.sh`.

## Quick start

```bash
git clone https://github.com/SutanuNandigrami/biblichor.git
cd biblichor
./deploy/bootstrap.sh
```

`bootstrap.sh` prompts for Gmail, your `@kindle.com` address, and
the BookOrbit admin credentials, then brings the whole stack up
(biblichor + FlareSolverr + BookOrbit + Postgres) and validates it
with `bookorbit-doctor`. ~5 minutes end to end.

When it's done:

| URL | What |
|---|---|
| `http://localhost:8090` | biblichor dashboard |
| `http://localhost:8090/library` | First-run BookOrbit setup wizard + sync surfaces |
| `http://localhost:3000` | BookOrbit (reader, Kobo, KOReader, OPDS) |
| `http://localhost:8090/healthz` | Component-level health probe |

## Documentation

Full guides live in [`docs/wiki/`](docs/wiki/) and are mirrored to
the [GitHub wiki](https://github.com/SutanuNandigrami/biblichor/wiki):

- **[Installation](docs/wiki/Installation.md)** — compose (easy) and
  native (advanced) paths
- **[Configuration](docs/wiki/Configuration.md)** — the two `.env`
  files, `config.yaml` reference, host UIDs, image pinning
- **[First-run walkthrough](docs/wiki/First-Run.md)** — adding
  sources, BookOrbit wizard, schedule, troubleshooting first jobs
- **[BookOrbit integration](docs/wiki/BookOrbit.md)** — URL model,
  doctor, metadata providers, API surface biblichor depends on
- **[Security](docs/wiki/Security.md)** — archive hygiene, ClamAV,
  encrypted credentials, recovery key
- **[Backups](docs/wiki/Backups.md)** — backup / restore, recovery
  key, encrypted secrets store
- **[Troubleshooting](docs/wiki/Troubleshooting.md)** — common
  problems and fixes
- **[Migrate from native](docs/wiki/Migrate-from-Native.md)** — the
  cutover from a systemd install to compose
- **[Architecture](docs/wiki/Architecture.md)** — pipeline diagram
  + data layout
- **[CLI reference](docs/wiki/CLI-Reference.md)** — every
  subcommand
- **[Bench + scrapers](docs/wiki/Bench-and-Scrapers.md)** — how
  scraper ranking works and how to bench changes

## Screenshots

<table>
  <tr>
    <td width="50%"><b>Queue</b><br><img src="docs/screenshots/dashboard.png" alt="Queue"></td>
    <td width="50%"><b>Sources</b><br><img src="docs/screenshots/sources.png" alt="Sources"></td>
  </tr>
  <tr>
    <td><b>Scrapers</b><br><img src="docs/screenshots/scrapers.png" alt="Scrapers"></td>
    <td><b>Mirrors (with bench)</b><br><img src="docs/screenshots/mirrors.png" alt="Mirrors"></td>
  </tr>
  <tr>
    <td><b>Scoring config</b><br><img src="docs/screenshots/scoring.png" alt="Scoring"></td>
    <td><b>Book detail (search trace + candidates)</b><br><img src="docs/screenshots/book-detail.png" alt="Book detail"></td>
  </tr>
</table>

## Stack

- **Backend** — Python 3.12, FastAPI, Pydantic v2, APScheduler,
  SQLite WAL, aiosmtplib, httpx, curl-cffi, BeautifulSoup, rapidfuzz
- **Anti-bot** — FlareSolverr, CloakBrowser stealth Chromium,
  Playwright
- **Frontend** — Vue 3 + Vite + TypeScript + Tailwind + shadcn-vue +
  Pinia + Vue Router + Lucide icons
- **Conversion** — Calibre CLI (`ebook-convert`, `ebook-meta`)
- **Reader / sync** — BookOrbit in its own container; biblichor
  drops books into a shared mount

## Acknowledgements

- [Anna's Archive](https://annas-archive.org) — without which none of
  this exists
- [zelestcarlyone/stacks](https://github.com/zelestcarlyone/stacks) —
  the Wikipedia mirror auto-refresh idea (`utils/domainupdater.py`)
- [bbrown430/endless-library](https://github.com/bbrown430/endless-library)
  and the [Anna's Archive userscript](https://greasyfork.org/) for
  download-strategy lore
- [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr),
  [Calibre](https://calibre-ebook.com/),
  [BookOrbit](https://bookorbit.app/), [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)

## License

GNU Affero General Public License v3.0 (AGPL-3.0) — see [LICENSE](LICENSE).

AGPL is strong copyleft with a network-use clause: if you run a
modified biblichor as a service that users access over a network,
you must make the source available to those users. Since this repo
is already public, simply linking to it satisfies that obligation.
Personal self-hosted use has no additional obligations.
