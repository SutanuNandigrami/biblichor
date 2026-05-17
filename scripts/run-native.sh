#!/usr/bin/env bash
# Native runner: starts the dashboard (uvicorn) for live smoke testing.
set -euo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate
mkdir -p data data/books data/logs
export CONFIG_PATH="${CONFIG_PATH:-$(pwd)/config/config.yaml}"
export LIBRARY_DB="${LIBRARY_DB:-$(pwd)/data/library.db}"
# Source .env if present so secrets land in env
if [ -f config/.env ]; then
  set -a; . config/config/.env 2>/dev/null || . config/.env; set +a
fi
TAILSCALE_IP="${TAILSCALE_IP:-127.0.0.1}"
PORT="${PORT:-8080}"
echo "==> http://${TAILSCALE_IP}:${PORT}/queue"
exec uvicorn "endless_library.app:entry" --factory \
  --host "$TAILSCALE_IP" --port "$PORT"
