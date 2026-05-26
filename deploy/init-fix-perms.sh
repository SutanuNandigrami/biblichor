#!/bin/sh
# init-fix-perms.sh — run inside the biblichor-fixperms init container.
#
# Self-heals the uid 1000/1001 hell. Runs on every `docker compose up`
# as root, chowns bind-mounted dirs to 1000:1000 (the biblichor container
# user, also matches bookorbit's PUID), and surgically preserves the
# postgres data dir at 999:999. POSIX sh only — runs unmodified on the
# alpine init image with no extra packages.
#
# Why this exists: bind-mounted dirs inherit the host user's uid (1001
# for ubuntu, github-runner; 1000 for opc). When that doesn't match the
# container's biblichor user (uid 1000), writes fail and biblichor's
# /healthz returns 503. Pre-init-container era: someone ran
# deploy/fix-perms.sh manually after every git pull / fresh clone / CI
# checkout. This script makes that automatic.

set -e

UID_TARGET="${BIBLICHOR_UID:-1000}"
GID_TARGET="${BIBLICHOR_GID:-1000}"
PG_UID="${BIBLICHOR_PG_UID:-999}"
PG_GID="${BIBLICHOR_PG_GID:-999}"

log() { printf 'biblichor-fixperms: %s\n' "$*"; }

log "target uid:gid = ${UID_TARGET}:${GID_TARGET} (postgres preserved at ${PG_UID}:${PG_GID})"

# library/: biblichor + bookorbit both write here, both run as 1000.
if [ -d /host/library ]; then
    chown -R "${UID_TARGET}:${GID_TARGET}" /host/library 2>/dev/null || true
    log "library/: chown -R done"
fi

# config/: biblichor reads + writes config.yaml (in-app changes).
if [ -d /host/config ]; then
    chown -R "${UID_TARGET}:${GID_TARGET}" /host/config 2>/dev/null || true
    log "config/: chown -R done"
fi

# data/: recursive chown EXCEPT data/bookorbit-db/ which must be the
# postgres uid (or the container refuses to start).
if [ -d /host/data ]; then
    chown "${UID_TARGET}:${GID_TARGET}" /host/data 2>/dev/null || true
    # shellcheck disable=SC2039
    find /host/data -mindepth 1 -maxdepth 1 ! -name bookorbit-db \
        -exec chown -R "${UID_TARGET}:${GID_TARGET}" {} + 2>/dev/null || true
    if [ -d /host/data/bookorbit-db ]; then
        chown -R "${PG_UID}:${PG_GID}" /host/data/bookorbit-db 2>/dev/null || true
        log "data/bookorbit-db/: restored to ${PG_UID}:${PG_GID}"
    fi
    log "data/: chown -R done (postgres dir preserved)"
fi

# deploy/compose.yml: the in-app BookOrbit upgrade flow writes this
# file. Must be writable by biblichor user (mode 664 + correct group).
if [ -f /host/deploy/compose.yml ]; then
    chown "${UID_TARGET}:${GID_TARGET}" /host/deploy/compose.yml 2>/dev/null || true
    chmod 664 /host/deploy/compose.yml 2>/dev/null || true
    log "deploy/compose.yml: chowned + chmod 664"
fi

log "done"
