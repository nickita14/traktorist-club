# syntax=docker/dockerfile:1
# Production image: gunicorn serving Django as a non-root user. Static files (with the Tailwind
# build) and the compiled locale are produced here, so the server needs no Node, no Tailwind
# binary and no gettext. See docs/deploy.md.

FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

# Dependencies only, into /opt/venv (outside /app, so compilemessages never walks into it).
FROM python:3.13-slim AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# Tailwind CSS, the .mo files and collectstatic, in production mode so collectstatic writes the
# manifest that ManifestStaticFilesStorage needs (placeholder settings: deploy/build.env).
FROM builder AS assets
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*
COPY . .
RUN set -a && . deploy/build.env && set +a \
    && /opt/venv/bin/python manage.py tailwind build \
    && /opt/venv/bin/python manage.py compilemessages \
    && /opt/venv/bin/python manage.py collectstatic --noinput

FROM python:3.13-slim AS runtime
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /srv/static \
    && chown app:app /srv/static
# ENVIRONMENT defaults to production: a container started without configuration fails the
# settings validation instead of running in development mode.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# Code, static files and translations stay owned by root: the app user can read, not change them.
COPY . .
COPY --from=assets /app/staticfiles /app/staticfiles
COPY --from=assets /app/locale /app/locale
USER app
EXPOSE 8000
ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
CMD ["gunicorn", "--config", "deploy/gunicorn.conf.py", "traktorist_club.wsgi"]
