#!/usr/bin/env bash
# Restore a pg_dump made by deploy/backup.sh.
#
#   deploy/restore.sh DUMP             drill: restore into a scratch database, compare row counts
#                                      with the live one, drop the scratch database. Changes nothing.
#   deploy/restore.sh --replace DUMP   replace the live database with DUMP: takes a safety dump
#                                      first, stops the web container while restoring.
#
# An off-site copy (*.dump.age) must be decrypted on your own machine first (docs/deploy.md).
set -Eeuo pipefail

APP_DIR=${APP_DIR:-/srv/traktorist}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/traktorist}
LIVE_DB=traktorist_club
SCRATCH_DB=traktorist_restore_check
# Row counts that must match between the dump and the live database in a drill.
TABLES=(club_player club_season club_game club_result auth_user otp_totp_totpdevice)

REPLACE=false
if [[ ${1:-} == --replace ]]; then
    REPLACE=true
    shift
fi
DUMP=${1:?usage: restore.sh [--replace] DUMP}
[[ -r $DUMP ]] || { echo "Cannot read $DUMP" >&2; exit 1; }

compose() {
    docker compose --project-directory "$APP_DIR" -f "$APP_DIR/compose.prod.yaml" "$@"
}

psql_db() {
    # $1: database, $2: SQL. Prints the bare result.
    compose exec -T -e PGOPTIONS=--client-min-messages=warning db \
        psql -U traktorist -d "$1" -v ON_ERROR_STOP=1 -Atc "$2"
}

restore_into() {
    # $1: database. The whole restore runs in one transaction: all or nothing.
    compose exec -T db pg_restore -U traktorist -d "$1" --clean --if-exists --no-owner \
        --single-transaction --exit-on-error <"$DUMP"
}

count_rows() {
    local db=$1 table
    for table in "${TABLES[@]}"; do
        printf '%s %s\n' "$table" "$(psql_db "$db" "SELECT count(*) FROM $table")"
    done
}

if ! $REPLACE; then
    drop_scratch() { psql_db postgres "DROP DATABASE IF EXISTS $SCRATCH_DB" >/dev/null; }
    trap drop_scratch EXIT
    drop_scratch
    psql_db postgres "CREATE DATABASE $SCRATCH_DB" >/dev/null
    restore_into "$SCRATCH_DB"

    echo "Rows per table (dump / live):"
    differ=0
    while read -r table restored && read -r _ live <&3; do
        mark=""
        if [[ $restored != "$live" ]]; then
            mark="  <- differs"
            differ=1
        fi
        printf '  %-22s %8s / %-8s%s\n' "$table" "$restored" "$live" "$mark"
    done < <(count_rows "$SCRATCH_DB") 3< <(count_rows "$LIVE_DB")
    if [[ $differ -eq 1 ]]; then
        echo "Restore drill OK. Counts differ: expected when the dump is older than today's edits."
    else
        echo "Restore drill OK: the dump restores cleanly and matches the live database."
    fi
    exit 0
fi

read -r -p "Replace the LIVE database with $DUMP? Type 'restore' to continue: " answer
[[ $answer == restore ]] || { echo "Cancelled."; exit 1; }

umask 077
mkdir -p "$BACKUP_DIR/pre-restore"
safety="$BACKUP_DIR/pre-restore/traktorist-$(date +%Y-%m-%d_%H%M%S).dump"
compose exec -T db pg_dump -U traktorist -d "$LIVE_DB" --format=custom >"$safety"
echo "Safety dump of the current database: $safety"

compose stop web
trap 'compose start web' EXIT
restore_into "$LIVE_DB"
echo "Restored $DUMP into $LIVE_DB."
count_rows "$LIVE_DB" | sed 's/^/  /'
