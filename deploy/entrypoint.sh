#!/bin/sh
# Container entrypoint: refuses to run outside production mode, and before gunicorn starts copies
# the image's collected static files into the volume Caddy serves /static/ from.
set -eu

if [ "${ENVIRONMENT:-}" != "production" ]; then
    echo "Refusing to start: ENVIRONMENT must be 'production' in this image (got '${ENVIRONMENT:-}')." >&2
    exit 1
fi

if [ "${1:-}" = "gunicorn" ]; then
    # Overwrite, never empty: pages cached in browsers may still ask for the previous hashed names.
    cp -R /app/staticfiles/. /srv/static/
fi

exec "$@"
