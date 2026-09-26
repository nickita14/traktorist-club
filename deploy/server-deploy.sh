#!/usr/bin/env bash
# The server half of a deploy, run by deploy/deploy.sh after it checked out the new code (it can
# also be run by hand on the server, from any directory):
#
#   build the image (pulling newer base, Postgres and Caddy images), run `check --deploy` in it, back up the database if migrations are pending,
#   migrate, start or recreate what changed and wait for the healthchecks, reload Caddy, prune.
#
# Every step is safe to repeat: an unchanged checkout builds from cache and changes nothing.
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/traktorist}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/traktorist}
export COMPOSE_FILE=${COMPOSE_FILE:-$APP_DIR/compose.prod.yaml}
# Build attestations carry a timestamp, so every build would get a new image ID and Compose would
# restart the app on each deploy even with nothing changed. (Compose ignores provenance: false.)
export BUILDX_NO_DEFAULT_ATTESTATIONS=1

compose() { docker compose --project-directory "$APP_DIR" "$@"; }

echo "==> Building"
# --pull: the Python and uv base images get their security fixes on the next deploy.
compose build --pull web
# Postgres 17 and Caddy 2 minor releases, by tag. Unchanged images leave the containers running.
compose pull --quiet db caddy

echo "==> Checking production settings"
compose run --rm --no-deps web python manage.py check --deploy --fail-level WARNING

compose up -d --wait db
if ! compose run --rm web python manage.py migrate --check >/dev/null 2>&1; then
    echo "==> Migrations pending: backing up first"
    (
        umask 077
        mkdir -p "$BACKUP_DIR/pre-deploy"
        compose exec -T db pg_dump -U traktorist -d traktorist_club --format=custom \
            >"$BACKUP_DIR/pre-deploy/traktorist-$(date +%Y-%m-%d_%H%M%S).dump"
    )
    # The last five are plenty; the nightly backups cover the rest.
    find "$BACKUP_DIR/pre-deploy" -maxdepth 1 -name 'traktorist-*.dump' -printf '%T@ %p\n' \
        | sort -rn | tail -n +6 | cut -d' ' -f2- | xargs -r rm --
    compose run --rm web python manage.py migrate --noinput
fi

echo "==> Starting"
compose up -d --wait --remove-orphans
# Picks up Caddyfile changes (a bind-mounted directory, so the new file is visible); a no-op
# when nothing changed.
compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null

echo "==> Pruning"
docker image prune -f >/dev/null
docker builder prune -f --filter until=168h >/dev/null
df -h / | tail -1 | awk '{print "Disk: " $3 " used, " $4 " free"}'
