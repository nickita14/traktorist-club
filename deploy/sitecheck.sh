#!/usr/bin/env bash
# Optional uptime check (traktorist-sitecheck.timer, every 5 minutes): when the site answers 200
# over HTTPS, send a heartbeat to the Cronitor monitor in MONITOR_SITE_URL; otherwise a failure
# ping (state=fail). If the whole server is down, the heartbeats stop and Cronitor alerts after
# the grace period. Does nothing while MONITOR_SITE_URL is empty.
set -euo pipefail

# shellcheck source=deploy/monitor-ping.sh
. "$(dirname "${BASH_SOURCE[0]}")/monitor-ping.sh"

APP_DIR=${APP_DIR:-/srv/traktorist}
set -a
# shellcheck source=/dev/null
. "$APP_DIR/.env"
set +a

[[ -n ${MONITOR_SITE_URL:-} ]] || exit 0

# Through the public name, so DNS, TLS and Caddy are checked along with the app.
if curl -fsS -m 15 -o /dev/null "https://$SITE_DOMAIN/robots.txt"; then
    monitor_ping "$MONITOR_SITE_URL"
else
    monitor_ping "$MONITOR_SITE_URL" fail
fi
