#!/usr/bin/env bash
# Nightly database backup (run by traktorist-backup.timer as the deploy user).
#
#   1. pg_dump (custom format) of the live database into $BACKUP_DIR, readable by its owner only
#   2. verify the dump: pg_restore must be able to read its whole table of contents
#   3. keep the newest $KEEP dumps (counted, not dated: a week of failures never empties the folder)
#   4. encrypt with age for $AGE_RECIPIENT (the private key is not on the server) and upload to
#      Backblaze B2 with a key that cannot delete
#   5. ping healthchecks.io: only after all of the above. No ping for 26 hours means an email.
#
# Any failure pings the check's /fail URL once, so a broken night is reported at once.
#
#   deploy/backup.sh               the full run
#   deploy/backup.sh --local-only  steps 1-3 only, no upload, no ping (before B2 is set up, drills)
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/traktorist}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/traktorist}
KEEP=${KEEP:-14}
LOCAL_ONLY=false
[[ ${1:-} == --local-only ]] && LOCAL_ONLY=true

set -a
# shellcheck source=/dev/null
. "$APP_DIR/.env"
set +a

compose() {
    docker compose --project-directory "$APP_DIR" -f "$APP_DIR/compose.prod.yaml" "$@"
}

ping_check() {
    # $1: "" for success, "/fail" for failure. Never fails the script itself.
    [[ -n ${HEALTHCHECKS_BACKUP_URL:-} ]] || return 0
    curl -fsS -m 10 --retry 5 -o /dev/null "${HEALTHCHECKS_BACKUP_URL}$1" || true
}

dump=""
encrypted=""
finish() {
    local status=$?
    if [[ -n $dump ]]; then rm -f -- "$dump.partial"; fi
    if [[ -n $encrypted ]]; then rm -f -- "$encrypted"; fi
    if [[ $status -ne 0 ]]; then
        echo "Backup FAILED (exit $status)." >&2
        $LOCAL_ONLY || ping_check /fail
    fi
}
# One place for cleanup and the failure ping, whatever step fails (set -e ends the run there).
trap finish EXIT

umask 077
mkdir -p "$BACKUP_DIR"
name="traktorist-$(date +%Y-%m-%d_%H%M%S).dump"
dump="$BACKUP_DIR/$name"

compose exec -T db pg_dump -U traktorist -d traktorist_club --format=custom >"$dump.partial"
tables=$(compose exec -T db pg_restore --list <"$dump.partial" | grep -c ' TABLE DATA ')
if [[ $tables -eq 0 ]]; then
    echo "The dump has no table data." >&2
    exit 1
fi
mv "$dump.partial" "$dump"
echo "Dump: $dump ($(du -h "$dump" | cut -f1), $tables tables)"

# Newest first; everything after the first $KEEP goes.
find "$BACKUP_DIR" -maxdepth 1 -name 'traktorist-*.dump' -printf '%T@ %p\n' \
    | sort -rn | tail -n +$((KEEP + 1)) | cut -d' ' -f2- \
    | while read -r old; do rm -f -- "$old" && echo "Removed $old"; done

if $LOCAL_ONLY; then
    echo "Local only: no upload, no healthchecks ping."
    exit 0
fi

encrypted="$dump.age"
age --encrypt --recipient "$AGE_RECIPIENT" --output "$encrypted" "$dump"
# The key may not list or read files, so rclone must not look before writing (--no-check-dest).
RCLONE_CONFIG_B2_TYPE=b2 \
    RCLONE_CONFIG_B2_ACCOUNT="$B2_KEY_ID" \
    RCLONE_CONFIG_B2_KEY="$B2_APPLICATION_KEY" \
    rclone --config /dev/null copyto --no-check-dest --retries 3 "$encrypted" "b2:$B2_BUCKET/$name.age"
echo "Uploaded b2:$B2_BUCKET/$name.age"

ping_check ""
echo "Backup complete."
