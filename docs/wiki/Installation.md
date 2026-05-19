# Installation

Two install paths: **Docker Compose** (recommended — one command,
~5 min) or **native systemd** (advanced — Python venv + Calibre on
the host).

## Prerequisites

| Component | Why |
|---|---|
| **Docker + docker compose** (compose path) OR **Python 3.12 + Node.js 20+ + Calibre** (native path) | Backend runtime + SPA build + format conversion |
| **An SMTP account** | Send books to Kindle (Gmail with an app password is the easy path) |
| **Amazon Kindle email setup** | Whitelist your sender, capture your `@kindle.com` address — <https://www.amazon.com/sendtokindle> |

Optional but recommended:

- **Tailscale** for private dashboard exposure
- **Pushover** for push notifications
- **A linux host with UID 1000 = your user** (e.g. Ubuntu cloud
  images). On other systems, see [Configuration → Host UIDs](Configuration#host-filesystem-requirements).

## Easy way — Docker Compose

```bash
git clone https://github.com/SutanuNandigrami/biblichor.git
cd biblichor
./deploy/bootstrap.sh
```

`bootstrap.sh` prompts for every secret it needs:

- Gmail account + **app password** (the SMTP-to-Kindle path)
- Your `@kindle.com` send-to-kindle address
- BookOrbit admin username / email / display name / password
- Optional: ports, timezone, ClamAV opt-in

It then writes a single `.env`, pulls + builds the services
(biblichor + FlareSolverr + BookOrbit + Postgres, plus optional
ClamAV), polls `/healthz` until biblichor reports green, polls
BookOrbit's `/api/v1/health` (cold Postgres migrations take ~60s on
first run), runs `biblichor bookorbit-setup` (which creates the
BookOrbit superuser, creates the watched library at `/library`, and
flips `bookorbit.enabled = true` in `config.yaml`), and finally
runs `biblichor bookorbit-doctor` to confirm the stack is healthy.

Re-run `./deploy/bootstrap.sh` anytime to reconfigure or refresh
containers. It's idempotent and keeps your existing values as
bracketed defaults.

**Useful commands once running:**

```bash
COMPOSE="docker compose -f deploy/compose.yml --env-file .env"

$COMPOSE logs -f biblichor       # tail logs
$COMPOSE restart biblichor       # restart biblichor only
$COMPOSE down                    # stop everything (data persists)
$COMPOSE up -d                   # start

biblichor bookorbit-setup        # idempotent — re-run to refresh
biblichor bookorbit-doctor       # 6-check health probe
biblichor migrate-to-bookorbit   # one-shot import from data/calibre-library/
biblichor backup --postgres-container biblichor-bookorbit-db \
                 --bookorbit-data ./data/bookorbit       # snapshot
```

## Advanced way — native install

Use this when you want biblichor as a systemd unit on the host,
with only FlareSolverr + BookOrbit + Postgres in Docker.

```bash
# 1. Clone
git clone https://github.com/SutanuNandigrami/biblichor.git
cd biblichor

# 2. Python venv + install
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

# 3. Calibre (Ubuntu/Debian shown; see calibre-ebook.com for other platforms)
sudo apt-get install -y calibre

# 4. Build the SPA (one-shot; rebuild only when you change webapp/src)
cd webapp && npm install && npm run build && cd ..

# 5. Config files
cp config/config.yaml.example config/config.yaml
cp .env.example config/.env

# Edit config/.env to set:
#   SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS
#   KINDLE_EMAIL                     (the @kindle.com address)
#   PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN  (optional)
#   HARDCOVER_TOKEN                  (only if you use Hardcover)
#   TAILSCALE_IP                     (optional dashboard bind)
#
# config/config.yaml mostly works as-is — you usually only need
# to change general.books_dir and general.timezone.

# 6. Whitelist your SMTP sender at:
#    https://www.amazon.com/hz/mycd/myx#/home/settings/payment
#    → Personal Document Settings → Approved Personal Document
#      E-mail List → add SMTP_USER

# 7. Start FlareSolverr + BookOrbit + Postgres
docker compose -f deploy/compose.yml up -d flaresolverr bookorbit-db bookorbit

# 8. Open biblichor in the foreground for a smoke test
. .venv/bin/activate
./scripts/run-native.sh
# Visit http://localhost:8090 (or http://<tailscale-ip>:8090).
# Add a source via the Sources page, click Run Now.
# Press Ctrl-C when satisfied.

# 9. Install as systemd
sudo cp scripts/endless-library.service.template /etc/systemd/system/biblichor.service
sudo sed -i "s|__USER__|$USER|; s|__PROJECT_ROOT__|$PWD|" \
    /etc/systemd/system/biblichor.service
sudo systemctl daemon-reload
sudo systemctl enable --now biblichor
sudo systemctl status biblichor
```

For first-run BookOrbit setup, use the SPA wizard at
`http://localhost:8090/library` — it covers admin creation, library
setup, and credential storage without any CLI step.

## Switching between install paths

- **Native → Compose**: see [Migrate from native](Migrate-from-Native).
  Bring data over, rewrite `books_dir`, then bootstrap.
- **Compose → Native**: less common. Stop the stack, restore the
  sqlite + library directory under the native paths, point
  `config.yaml` at them, start the systemd unit.

In both directions, BookOrbit's Postgres data lives in a docker
volume; if you want to keep the BookOrbit state, keep the volume.

## What you should see after install

- `http://localhost:8090` — biblichor dashboard, queue page loads
- `http://localhost:8090/library` — either the BookOrbit setup
  wizard (if first run) or the sync surfaces (after setup)
- `http://localhost:8090/healthz` returns `{"ok": true, ...}`
- `biblichor bookorbit-doctor` returns 6 green checks
