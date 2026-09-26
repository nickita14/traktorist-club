#!/usr/bin/env bash
# Deploy from your own machine: deploy/deploy.sh [REF]
#
# Over SSH: refuse if the server checkout has local edits, check out REF (default origin/main),
# then run that version's deploy/server-deploy.sh (build, check --deploy, backup when migrations
# are pending, migrate, restart, healthchecks, prune). Then, from here: the site must answer 200
# over HTTPS, send HSTS, and redirect plain HTTP to HTTPS. Running it twice in a row is harmless.
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

echo "==> Smoke check https://$DOMAIN"
status=$(curl -sS -o /dev/null -w '%{http_code}' -L --max-redirs 3 "https://$DOMAIN/")
[[ $status == 200 ]] || { echo "Home page answered $status" >&2; exit 1; }
headers=$(curl -sS -D - -o /dev/null "https://$DOMAIN/robots.txt")
grep -q '^HTTP/[0-9.]* 200' <<<"$headers" || { echo "robots.txt is not 200" >&2; exit 1; }
grep -qi '^strict-transport-security:' <<<"$headers" \
    || { echo "No HSTS header: is the app in production mode?" >&2; exit 1; }
redirect=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "http://$DOMAIN/")
[[ $redirect == 30[18]\ https://* ]] || { echo "HTTP does not redirect to HTTPS: $redirect" >&2; exit 1; }
echo "==> Deployed: home 200, robots.txt 200 with HSTS, HTTP redirects to HTTPS"
