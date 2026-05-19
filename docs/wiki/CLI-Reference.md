# CLI reference

All subcommands of `biblichor` (also runnable as
`python -m endless_library`). On compose installs, wrap with:

```bash
docker compose -f deploy/compose.yml --env-file .env exec biblichor \
    python -m endless_library <subcommand>
```

## Run

| Subcommand | What |
|---|---|
| `run` | Start the full FastAPI app + scheduler. Used by the systemd unit. |
| `run-once` | One poll + process pass, then exit. Good for cron-only deployments. |

## Status / inspection

| Subcommand | What |
|---|---|
| `status` | Print queue counts by state. |
| `bench [--quick]` | Run the scraper bench against `bench/queries.yaml`. Stores results in `bench_runs`. |

## BookOrbit

| Subcommand | What |
|---|---|
| `bookorbit-setup` | First-run admin + library creation. Idempotent — re-run anytime to refresh. Stores credentials encrypted in `library.db`. |
| `bookorbit-doctor` | 6-check stack health probe. Surfaces URL drift, library_root path mismatches, DTO drift, auth failures. |
| `migrate-to-bookorbit` | One-shot import from `data/calibre-library/` (legacy) into BookOrbit's watched library. |

## Backup / restore

| Subcommand | What |
|---|---|
| `backup` | Encrypted snapshot of sqlite + library files (+ optional Postgres + BookOrbit data). |
| `backup-key` | Show or generate the age recovery key. `--rotate` to rotate. `--self-mail` to email a copy to yourself. |
| `restore <bundle>` | Decrypt + restore from a `backup` output. |
| `restore-postgres <dump>` | Restore a pg_dump SQL file into the running `bookorbit-db`. |

## Send / files

| Subcommand | What |
|---|---|
| `send <file>` | Email a single file to your Kindle, bypassing the pipeline. Good for one-off sends. |
| `resend <book_id>` | Re-email a book that's already in the queue. |
| `repair-filenames` | Rename file_path values in the books table to match disk reality (after a manual mv). |

## Storage

| Subcommand | What |
|---|---|
| `storage` | Manage downloaded files. Subcommands: `list`, `orphan`, `delete`, `verify`. |

## Quick examples

```bash
# Watch the queue from the CLI
biblichor status

# Probe BookOrbit's health
biblichor bookorbit-doctor

# Mint a fresh backup
biblichor backup --postgres-container biblichor-bookorbit-db \
                 --bookorbit-data ./data/bookorbit

# Send a one-off file
biblichor send ~/Downloads/some-book.epub

# Re-run scraper bench after toggling strategies
biblichor bench --quick
```

## Flags every subcommand accepts

| Flag | What |
|---|---|
| `--config <path>` | Override `config.yaml` location |
| `--db <path>` | Override `library.db` location |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Operation failed (see stderr) |
| 2 | Argument error |
| 3 | External service unreachable (e.g. doctor probe failed) |
