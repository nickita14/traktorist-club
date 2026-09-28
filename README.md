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

Unfold ships no Russian strings; the project's own are in `src/locale/ru/LC_MESSAGES/django.po`. Compile them with `uv run python manage.py compilemessages --ignore=.venv` (needs `gettext`: `sudo apt install gettext`); without that step a few admin labels stay in English.

## Organizer accounts

Editing happens in the admin at `ADMIN_URL`. The `Organizer` group (created by a migration) can view, add and change players, seasons, games, results and blind structures, and delete results and structure rows. Deleting players, seasons, games or whole structures is left to superusers. An organizer account also needs **Staff status** (`is_staff`), which a group cannot grant:

```bash
uv run python manage.py createsuperuser   # first account; then add organizers in the admin
```

Two-factor login (a code from an authenticator app, TOTP) is available but off by default, in production too. To turn it on, first give every staff user a device with `manage.py totp_enroll <username>` (it prints a QR code in the terminal), then set `ADMIN_REQUIRE_2FA=True`. Five failed logins for the same username from the same address lock them out for an hour (`django-axes`).

## Blind timer

A tournament can run a blind timer: structures are templates in the admin (a season's default is preselected at the start), and each game gets its own copy that the organizer edits on the phone. The display for a laptop at the table opens from the live game screen; its "Ссылка на табло" is a secret read-only link (no login) that a new link revokes.

## Importing the spreadsheet

The source `.xlsx` lives in `data/` (gitignored) and never leaves your machine or the server.

1. List the raw player names per sheet:
   `uv run python manage.py list_sheet_names data/<file>.xlsx`
2. Copy `aliases.example.yaml` to `data/aliases.yaml` and map every raw name that has games to a canonical player.
3. Preview (nothing is saved): dates fixed, rows that would change, and a verification report against the sheet's own totals:
   `uv run python manage.py import_sheet data/<file>.xlsx --aliases data/aliases.yaml --dry-run`
4. Run the same command without `--dry-run`. Rerunning is safe: nothing is duplicated.

Options: `--sheets ТУР2025,КЭШ2025` imports only those sheets; `--create-missing` creates players for unknown names instead of failing.

**Once games are entered in the app, do not re-import the current season.** The sheet wins on a re-import: buy-ins, payouts and places are overwritten from the sheet, and results missing from the sheet are deleted. Use `--sheets` to re-import only finished seasons.

## Deploy

Production runs with Docker Compose (`compose.prod.yaml`: Caddy, gunicorn, Postgres 17) on a small VPS. [`docs/deploy.md`](docs/deploy.md) is the runbook: server setup, deploys (`deploy/deploy.sh`), backups and restores, and the first data load.

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
