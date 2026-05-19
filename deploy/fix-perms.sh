#!/usr/bin/env bash
# Phase 6o.10: chown biblichor bind mounts safely.
#
# The biblichor container runs as UID 1000 (Phase 6o.5). A naive
#   sudo chown -R 1000:1000 .
# from the repo root will brick postgres because
#   data/bookorbit-db/ MUST be owned by UID 999 (the postgres user
#   inside the official postgres image).
#
# This script does the right thing: chown all biblichor-owned dirs
# to 1000:1000, and restore data/bookorbit-db to 999:999 if it got
# caught in the crossfire.

set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null; then
    echo "fix-perms.sh: needs sudo (the chown targets are not user-owned)"
    echo "  re-run as: sudo $0   or   make sure passwordless sudo is configured"
    exit 1
fi

echo "[1/3] chown biblichor bind mounts to UID 1000 (container user)..."
sudo chown -R 1000:1000 config library 2>/dev/null || true

# data has subdirs we DO want at 1000 (sqlite, logs, etc.) and one
# we MUST leave at 999 (bookorbit-db = postgres data dir)
if [ -d data ]; then
    sudo find data -mindepth 1 -maxdepth 1 ! -name 'bookorbit-db' \
        -exec chown -R 1000:1000 {} + 2>/dev/null || true
fi

echo "[2/3] restore postgres data dir to UID 999 (postgres user)..."
if [ -d data/bookorbit-db ]; then
    sudo chown -R 999:999 data/bookorbit-db
fi

echo "[3/3] verify..."
echo "  $(stat -c '%U:%G %n' config 2>/dev/null || echo 'config (missing)')"
echo "  $(stat -c '%U:%G %n' library 2>/dev/null || echo 'library (missing)')"
echo "  $(stat -c '%U:%G %n' data 2>/dev/null || echo 'data (missing)')"
if [ -d data/bookorbit-db ]; then
    echo "  $(stat -c '%U:%G %n' data/bookorbit-db)"
fi

echo
echo "Done. Now restart the stack:"
echo "  docker compose -f deploy/compose.yml --env-file .env up -d"
