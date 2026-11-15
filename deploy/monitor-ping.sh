# shellcheck shell=bash
# Heartbeat pings to the monitoring service (Cronitor), sourced by backup.sh and sitecheck.sh.
#
# The URL holds the telemetry key, so it is never printed: no set -x around it, no echo, curl's
# body goes to /dev/null and -s keeps it quiet on success. curl's error line stays on stderr (the
# journal): it names at most the host, never the path or the query.

# monitor_ping <url> [fail]
#   A bare request is a heartbeat; "fail" adds state=fail to the query (never "/fail" to the path:
#   that would be another monitor). An empty url does nothing. Never fails the caller.
monitor_ping() {
    local url=$1
    [[ -n $url ]] || return 0
    if [[ ${2:-} == fail ]]; then
        case $url in
            *\?) url+="state=fail" ;;
            *\?*) url+="&state=fail" ;;
            *) url+="?state=fail" ;;
        esac
    fi
    curl -fsS -m 10 --retry 5 -o /dev/null -- "$url" || true
}
