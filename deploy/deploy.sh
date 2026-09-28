#!/usr/bin/env bash
# Deploy from your own machine: deploy/deploy.sh [REF]
#
# Over SSH: refuse if the server checkout has local edits, check out REF (default origin/main),
# then run that version's deploy/server-deploy.sh (build, check --deploy, backup when migrations
# are pending, migrate, restart, healthchecks, prune). Then, from here: the site must answer 200
# over HTTPS, send HSTS, and redirect plain HTTP to HTTPS (deploy/smoke-check.sh). Running it twice
# in a row is harmless. Pushes to main are deployed by CI (deploy/ci-deploy.sh); this script is for
# manual deploys and rollbacks.
#
#   DEPLOY_HOST    ssh target (default deploy@traktorist.duckdns.org)
#   DEPLOY_DOMAIN  public name for the smoke check (default traktorist.duckdns.org)
set -euo pipefail

HOST=${DEPLOY_HOST:-deploy@traktorist.duckdns.org}
DOMAIN=${DEPLOY_DOMAIN:-traktorist.duckdns.org}
REF=${1:-origin/main}

echo "==> Deploying $REF to $HOST"
ssh "$HOST" bash -s -- "$REF" <<'REMOTE'
set -euo pipefail
cd /srv/traktorist
# Held until server-deploy.sh exits (exec keeps the descriptor): a CI deploy
# (deploy/ci-deploy.sh) waits for this one, and this one for it.
exec 9>.git/traktorist-deploy.lock
flock -w 900 9 || { echo "Another deploy has held the lock for 15 minutes; giving up" >&2; exit 1; }
if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    echo "The server checkout has local changes; refusing to deploy:" >&2
    git status --short >&2
    exit 1
fi
git fetch --quiet --prune --tags origin
git checkout --quiet --detach "$1"
echo "==> At $(git log -1 --format='%h %s')"
# The freshly checked-out version of the script does the rest.
exec deploy/server-deploy.sh
REMOTE

"$(dirname "$0")/smoke-check.sh" "$DOMAIN"
echo "==> Deployed $REF"
