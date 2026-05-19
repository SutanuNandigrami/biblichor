# Troubleshooting

Symptoms first, fixes alongside. If your symptom isn't here,
open an issue with what you see in `docker compose logs biblichor`
+ the output of `biblichor bookorbit-doctor`.

## Dashboard

| Symptom | Likely cause | Fix |
|---|---|---|
| `/api/...` returns 200 but the SPA shows blank pages | SPA not built | `cd webapp && npm install && npm run build` (native) or rebuild the biblichor image (compose) |
| Dashboard returns 404 for all routes | uvicorn pointed at wrong dir | Check `WorkingDirectory` in the systemd unit |
| `/healthz` returns 503 | A component is unready | Inspect the JSON body — `db`, `scrapers`, `scheduler` flags tell you which |
| Settings page doesn't save | Container can't write `config/config.yaml` | Run `deploy/fix-perms.sh` |

## Acquisition pipeline

| Symptom | Likely cause | Fix |
|---|---|---|
| Books stuck in `needs_review` | Auto-pick gap too tight or scoring strict | Lower `auto_pick_gap` or raise candidate scores in /scoring |
| `403` on Anna's | Cloudflare challenge active for your IP | Confirm FlareSolverr is up (`docker compose ps`); check `flaresolverr_url` in config |
| Welib downloads always fail | Cloudflare Turnstile blocking | Set `WELIB_AUTH_COOKIE` from a logged-in browser session |
| LibGen always 404 | Mirror outage; biblichor cycles through libgen.li/.is/.rs/.st | Wait, or temporarily disable in /scrapers |
| Source not polling | Schedule job paused | Resume from the Schedule page |
| Source polls but no books appear | Goodreads shelf privacy or stale ID | Confirm the shelf is public and the URL/ID is correct |

## Kindle send

| Symptom | Likely cause | Fix |
|---|---|---|
| `(0, b'')` Gmail SMTP error | Gmail rejected the app password | Strip any spaces; regenerate the app password at <https://myaccount.google.com/apppasswords> |
| 250 OK but nothing arrives | Sender not whitelisted | Add `SMTP_USER` to Approved Personal Document E-mail List on Amazon |
| Attachment too large | Kindle's per-file cap (default 50 MB) | Set `kindle.attachment_max_mb` in `config.yaml` |
| Conversion fails with "ebook-convert not found" | Calibre missing | `sudo apt install calibre` on the host (native) or rebuild image (compose includes it) |

## BookOrbit

| Symptom | Likely cause | Fix |
|---|---|---|
| Books arrive on Kindle but not in BookOrbit | `bookorbit.enabled=false` or `library_root` mismatch | Run `biblichor bookorbit-doctor` — it tells you which |
| Library page shows "First-run setup needed" forever | BookOrbit not reachable | Confirm `docker compose ps` shows bookorbit healthy; check the URL in `config.yaml` |
| "Open BookOrbit" link goes to localhost on your phone | Old code; should be fixed now | If you see this, run `git pull && docker compose build biblichor && docker compose up -d biblichor` |
| Scan button returns 400 "no library_id" | First-run setup didn't complete | Re-run the Library page wizard or `biblichor bookorbit-setup` |
| Scan returns 400 "credentials not stored" | Creds cleared | Click **Store creds** on the Library page or re-run setup |
| Doctor flags `library_root.exists=false` | Path biblichor sees doesn't exist (e.g. host vs container mismatch) | Set `bookorbit.library_root: /library` in `config.yaml` (compose) |
| Doctor flags `setup_status.dto` failed | BookOrbit upgraded and renamed `needsSetup` | Pin the old digest in `compose.yml` and open an issue |
| Postgres won't start: "Permission denied" on relmapper / pg_filenode | Host data dir got chowned to UID 1000 instead of 999 | `sudo chown -R 999:999 data/bookorbit-db` |

## Permissions

| Symptom | Likely cause | Fix |
|---|---|---|
| biblichor container restarts, log shows "Permission denied: /app/config/.env" | Host file not readable by UID 1000 | `deploy/fix-perms.sh` |
| Books appear in `library/` but BookOrbit can't read them | Mount owned by wrong UID | `deploy/fix-perms.sh` |
| `chown -R 1000:1000 .` bricked postgres | Done that. The blanket chown overrode bookorbit-db | `sudo chown -R 999:999 data/bookorbit-db` |

## Scheduler

| Symptom | Likely cause | Fix |
|---|---|---|
| Scheduler jobs don't fire | systemd unit running an old build | `sudo systemctl restart biblichor` (or compose `restart`) |
| Run Now button does nothing | Job paused; click Resume on the table | |
| Every poll job runs at the same time | All sources still on default interval | Use the Schedule page → Reschedule to stagger |

## Useful diagnostic commands

```bash
COMPOSE="docker compose -f deploy/compose.yml --env-file .env"

$COMPOSE logs -f biblichor                    # tail biblichor logs
$COMPOSE logs -f bookorbit                    # tail BookOrbit logs
$COMPOSE ps --format "table {{.Service}}\t{{.Status}}"  # service health

biblichor status                              # queue counts by state
biblichor bookorbit-doctor                    # 6-check stack health
biblichor bench --quick                       # ranked scraper benchmark
biblichor backup --dry-run                    # what the next backup would include
```

## Reset to clean state

If everything is broken and you just want a clean install (keeping
backups):

```bash
$COMPOSE down -v             # -v drops postgres volume
sudo rm -rf data/ library/   # nuke biblichor state
./deploy/bootstrap.sh        # start fresh
biblichor restore data/backups/<latest>.tar.zst.age   # if you have one
```

Make sure you have a recent backup AND your recovery key before
running `down -v`.
