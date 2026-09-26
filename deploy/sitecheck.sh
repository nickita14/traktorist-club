#!/usr/bin/env bash
# Optional uptime check (traktorist-sitecheck.timer, every 5 minutes): when the site answers 200
# over HTTPS, ping the healthchecks.io check in HEALTHCHECKS_SITE_URL. If the site, Caddy, the
# certificate or the whole server is down, the pings stop and healthchecks.io sends an email.
# Does nothing while HEALTHCHECKS_SITE_URL is empty.
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/traktorist}
set -a
# shellcheck source=/dev/null
. "$APP_DIR/.env"
set +a

[[ -n ${HEALTHCHECKS_SITE_URL:-} ]] || exit 0

# Through the public name, so DNS, TLS and Caddy are checked along with the app.
if curl -fsS -m 15 -o /dev/null "https://$SITE_DOMAIN/robots.txt"; then
    curl -fsS -m 10 --retry 3 -o /dev/null "$HEALTHCHECKS_SITE_URL" || true
else
    curl -fsS -m 10 --retry 3 -o /dev/null "$HEALTHCHECKS_SITE_URL/fail" || true
fi
