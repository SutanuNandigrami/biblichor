#!/usr/bin/env bash
# Sync docs/wiki/ into the GitHub wiki repo.
#
# Source of truth lives in docs/wiki/ in this repo. The GitHub wiki
# is a clone-and-push mirror so visitors get nice rendering at
# github.com/<owner>/<repo>/wiki/<Page>.
#
# Usage:
#   scripts/sync-wiki.sh                  # uses origin's wiki repo
#   scripts/sync-wiki.sh <wiki-git-url>   # explicit wiki repo URL
#
# Requires: git, GitHub Wiki enabled on the project (Settings →
# Features → Wikis), push access to the wiki repo.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d docs/wiki ]]; then
    echo "sync-wiki: docs/wiki/ not found — nothing to sync" >&2
    exit 1
fi

if [[ $# -ge 1 ]]; then
    WIKI_URL="$1"
else
    # Derive wiki URL from origin
    ORIGIN=$(git remote get-url origin 2>/dev/null || true)
    if [[ -z "$ORIGIN" ]]; then
        echo "sync-wiki: no origin remote and no URL passed" >&2
        echo "  usage: $0 <wiki-git-url>" >&2
        exit 1
    fi
    # github.com/foo/bar.git -> github.com/foo/bar.wiki.git
    # github.com:foo/bar.git -> github.com:foo/bar.wiki.git
    WIKI_URL="${ORIGIN%.git}.wiki.git"
fi

echo "[1/4] cloning wiki repo from $WIKI_URL"
WORK=$(mktemp -d)
trap "rm -rf $WORK" EXIT
git clone --quiet "$WIKI_URL" "$WORK/wiki" || {
    echo
    echo "sync-wiki: clone failed. Common causes:" >&2
    echo "  - GitHub Wiki not enabled (Settings -> Features -> Wikis)" >&2
    echo "  - No initial wiki page created yet (visit the Wiki tab and create the Home page first)" >&2
    echo "  - SSH agent not set up for github.com" >&2
    exit 1
}

echo "[2/4] copying docs/wiki/*.md -> wiki repo"
# Wipe the wiki working tree but keep .git so we get a real diff
find "$WORK/wiki" -mindepth 1 -maxdepth 1 ! -name ".git" -exec rm -rf {} +
cp docs/wiki/*.md "$WORK/wiki/"

echo "[3/4] commit"
# Inherit author identity from the main repo's last commit so this
# works on a fresh VM / CI runner without git config --global.
MAIN_DIR=$(pwd)
AUTHOR=$(git -C "$MAIN_DIR" log -1 --format="%an")
EMAIL=$(git -C "$MAIN_DIR" log -1 --format="%ae")
SHA=$(git -C "$MAIN_DIR" rev-parse --short HEAD)
cd "$WORK/wiki"
git -c user.name="$AUTHOR" -c user.email="$EMAIL" -c init.defaultBranch=master add -A
if git diff --cached --quiet; then
    echo "  no changes — wiki already in sync"
    exit 0
fi
git -c user.name="$AUTHOR" -c user.email="$EMAIL" commit     -m "Sync wiki from main @ $SHA" >/dev/null

echo "[4/4] push"
git push origin HEAD
echo
echo "wiki synced. Visit your project's Wiki tab to verify."
