# traktorist-club

A Django app for a club's poker records: tournament and cash games, buy-ins, payouts, places, and season standings. Anyone with the link can read; only organizers can edit.

## Local setup

Requires `uv` and Docker. Python 3.13 is pinned in `.python-version`.

```bash
cp .env.example .env
uv sync                                      # installs app and dev dependencies
uv run pre-commit install
docker compose up -d db                      # local Postgres 17 on 127.0.0.1:5432
uv run python manage.py migrate
uv run python manage.py tailwind runserver   # dev server with Tailwind watcher
```

The first `tailwind` command downloads the standalone Tailwind binary (no Node needed).

## Organizer accounts

Editing happens in the admin at `ADMIN_URL`. The `Organizer` group (created by a migration) can view, add and change players, seasons, games and results, and delete results. Deleting players, seasons or games is left to superusers. An organizer account also needs **Staff status** (`is_staff`), which a group cannot grant:

```bash
uv run python manage.py createsuperuser   # first account; then add organizers in the admin
```

## Checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files
```

## Notes

- No real club data is committed. `data/` is gitignored; tests and fixtures use invented players.
- `CLAUDE.md` (project rules) and `PLAN.md` (roadmap) are kept locally and are not part of the repository.
- The public site has no external trackers, analytics, or CDNs.
