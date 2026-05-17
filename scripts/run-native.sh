#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/endless-library
# shellcheck disable=SC1091
. .venv/bin/activate
mkdir -p data data/books data/logs data/cookies
export CONFIG_PATH="${CONFIG_PATH:-/home/ubuntu/endless-library/config/config.yaml}"
export LIBRARY_DB="${LIBRARY_DB:-/home/ubuntu/endless-library/data/library.db}"
exec uvicorn endless_library.app:entry --factory \
  --host "${TAILSCALE_IP:-100.95.138.44}" --port "${PORT:-8090}"
