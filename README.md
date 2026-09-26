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
