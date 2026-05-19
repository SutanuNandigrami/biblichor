# biblichor wiki

Self-hosted Goodreads → Anna's Archive → Kindle automation, with an
integrated BookOrbit library (reader, Kobo sync, KOReader sync,
OPDS catalog). See [the repo README](https://github.com/SutanuNandigrami/biblichor)
for the elevator pitch.

## Where to start

| If you want to... | Read |
|---|---|
| Install biblichor for the first time | [Installation](Installation) |
| Add your first reading list and watch books flow | [First-run walkthrough](First-Run) |
| Understand which config knob does what | [Configuration](Configuration) |
| Set up BookOrbit (or fix it when it breaks) | [BookOrbit integration](BookOrbit) |
| Migrate from a native systemd install | [Migrate from native](Migrate-from-Native) |
| Recover from a lost server | [Backups](Backups) |
| Diagnose a stuck book / job | [Troubleshooting](Troubleshooting) |
| Tune scrapers / understand the pipeline | [Bench and scrapers](Bench-and-Scrapers) |
| Run something from the command line | [CLI reference](CLI-Reference) |
| Read the system architecture | [Architecture](Architecture) |
| Lock things down (archive hygiene, AV, credentials) | [Security](Security) |

## What changed recently

This wiki tracks the user-facing experience, not the implementation
history. For a chronological view of changes, see the
[git log](https://github.com/SutanuNandigrami/biblichor/commits/main).
Highlights worth noting:

- **BookOrbit setup is now a wizard in the SPA** — the first time
  you visit `/library` after install, biblichor walks you through
  admin creation, library setup, and credential storage. The CLI
  command still works for headless installs.
- **Credentials live encrypted in `library.db`** — under a symmetric
  key biblichor generates on first use. No plaintext passwords in
  `.env` files, no key files to remember to copy at migration time
  beyond the existing backup recovery key.
- **The "links go to localhost" gotcha is gone** — BookOrbit's
  dashboard / OPDS / Kobo / KOReader URLs are computed from the
  hostname your browser is on. Tailscale names, LAN IPs, and
  localhost all work without per-device config.
- **`deploy/fix-perms.sh`** safely fixes host filesystem ownership
  without bricking postgres (don't use a blanket `chown -R`).
- **`biblichor bookorbit-doctor`** probes the 5 endpoints biblichor
  depends on and surfaces drift before user-facing breakage. The
  same probe runs from the `/library` page's "Run doctor" button.

## Conventions

- Code blocks assume you're at the repo root unless otherwise noted.
- Docker compose examples use `docker compose -f deploy/compose.yml
  --env-file .env`; you can alias this to taste.
- "The compose stack" means biblichor + flaresolverr + bookorbit +
  bookorbit-db.
- "The SPA" means biblichor's Vue dashboard, served by FastAPI at
  port 8090.
