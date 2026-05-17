#!/usr/bin/env bash
# Launch the FastAPI server (which embeds the APScheduler) under uvicorn.
# Works from any path — resolves paths relative to the script location.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

# Activate the virtualenv if it exists; otherwise assume PATH is right.
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

mkdir -p data data/books data/logs data/cookies

export CONFIG_PATH="${CONFIG_PATH:-${PROJECT_ROOT}/config/config.yaml}"
export LIBRARY_DB="${LIBRARY_DB:-${PROJECT_ROOT}/data/library.db}"

# Bind: default to all interfaces so a fresh install just works. Override with
# TAILSCALE_IP=<ip> in your environment to lock the dashboard to a Tailscale
# IP, or HOST=<ip> for any other interface.
HOST="${TAILSCALE_IP:-${HOST:-0.0.0.0}}"
PORT="${PORT:-8090}"

exec uvicorn endless_library.app:entry --factory --host "${HOST}" --port "${PORT}"
