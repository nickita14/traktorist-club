#!/usr/bin/env bash
# Checks a deployed site from the outside: deploy/smoke-check.sh DOMAIN
#
# The home page must answer 200 over HTTPS, robots.txt 200 with HSTS (the app is in production
# mode), and plain HTTP must redirect to HTTPS. Run by deploy/deploy.sh and by the deploy job in
# .github/workflows/ci.yml; a failed check exits non-zero.
set -euo pipefail

DOMAIN=${1:?usage: deploy/smoke-check.sh DOMAIN}

echo "==> Smoke check https://$DOMAIN"
status=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' -L --max-redirs 3 "https://$DOMAIN/")
[[ $status == 200 ]] || { echo "Home page answered $status" >&2; exit 1; }
headers=$(curl -sS -m 20 -D - -o /dev/null "https://$DOMAIN/robots.txt")
grep -q '^HTTP/[0-9.]* 200' <<<"$headers" || { echo "robots.txt is not 200" >&2; exit 1; }
grep -qi '^strict-transport-security:' <<<"$headers" \
    || { echo "No HSTS header: is the app in production mode?" >&2; exit 1; }
redirect=$(curl -sS -m 20 -o /dev/null -w '%{http_code} %{redirect_url}' "http://$DOMAIN/")
[[ $redirect == 30[18]\ https://* ]] || { echo "HTTP does not redirect to HTTPS: $redirect" >&2; exit 1; }
echo "==> Smoke check passed: home 200, robots.txt 200 with HSTS, HTTP redirects to HTTPS"
