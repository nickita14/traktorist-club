# PLAN.md

Roadmap. Work one stage at a time, in order. Each stage ends with passing tests, ruff clean, and ticked checkboxes. Rules and domain definitions are in `CLAUDE.md`.

## Stage 1. Skeleton

- [x] Django project with `uv`, Python 3.13, Postgres via `docker compose`
- [x] Settings from env (`django-environ`), `.env.example` with dummy values, split dev/prod behavior by env flags
- [x] `ruff`, `pytest-django`, `pre-commit` (ruff, gitleaks, end-of-file fixes)
- [x] GitHub Actions: lint, tests (with a Postgres service), `check --deploy` against prod-like env
- [x] `.gitignore` includes `data/`, `.env`, `*.xlsx`, dumps
- [x] Dependabot for pip and GitHub Actions
- [x] README: what the project is, how to run locally (no real data, no club names beyond the project title)

Done when: fresh clone + commands from `CLAUDE.md` gives a running empty site and green CI.

## Stage 2. Models and admin

- [x] `club` app: `Player`, `Season`, `Game`, `Result` as described in `CLAUDE.md`
- [x] Constraints: `Season (year, kind)` unique; `Result (game, player)` unique; `place` only allowed for `tour` seasons, `chips_out` only for `cash` (model `clean()` + DB check constraint where practical)
- [x] `club/stats.py`: queryset helpers for player season totals (net, games, ITM, 1st/2nd/3rd), game totals (buy-ins, payouts, leftover), all-time totals
- [x] Place suggestion helper: rank by payout among positive payouts, top `paid_places`
- [x] Unfold admin: Season list filtered by kind/year; Game admin with inline Results; computed columns from `stats.py`
- [x] "Organizer" group with add/change permissions; data migration that creates it
- [x] Tests for every helper in `stats.py`, including ties and players with zero payout

## Stage 3. Spreadsheet import

Commands: `manage.py list_sheet_names <path.xlsx>` (raw names per sheet, to write the aliases file), then `manage.py import_sheet <path.xlsx> --aliases <aliases.yaml> [--dry-run] [--create-missing] [--sheets A,B]`. Source files live in `data/` (gitignored).

Source structure (same on all sheets):
- Sheets: `ТУР2025`, `КЭШ2025`, `ТУР2026_new`, `КЭШ2026` (any `ТУРyyyy`/`КЭШyyyy`, optional `_new`). **Ignore `ТУР2026_old version`.** Season year and kind come from the sheet name.
- Row 1: headers. Game dates are in row 1, every second column. Data columns start at `I` on `ТУР2026_new`, `H` on `ТУР2025`, `E` on both `КЭШ` sheets.
- Each player occupies **two rows**: first row has the name in column `B` and the buy-in under the game's date column (the next column holds a net formula, ignore it); second row has the payout under the same date column.
- An empty buy-in cell means the player did not play that game.
- Player rows end at the first row where column `B` or `D` reads `Закупка` (`ТУР2025` has it in `D`, like the cash sheets). Everything below is summaries (including the cash fund entries for Stage 8): ignore it.

Known data problems to handle:
- [x] **Dates**: some cells have wrong years (2025 on a 2026 sheet, year `0202`). Always take the year from the sheet name, day and month from the cell. Log every fixed date.
- [x] **Names** differ between seasons (e.g. `Имя` vs `Имя - ник` vs `Имя (ник)`). Map raw names to canonical players through `aliases.yaml`. Unknown names fail the import with a clear list, unless `--create-missing` is passed. Commit only `aliases.example.yaml` with fake names.
- [x] **Places** (tour only): compute with the place suggestion helper from payouts. 2025 places were entered by hand in the sheet, 2026 by formula: store computed places, then compare per-player counts of 1st/2nd/3rd with sheet columns and report differences.
  - Note on ties: the sheet counts places with `LARGE(payouts, k)` per k, so tied payouts are counted in several places at once (`300, 300, 100` gives both players a 1st AND a 2nd place). Our competition ranking gives `1, 1, 3`. The verification must report tie-related mismatches separately from real mismatches, labelled as expected.

Behavior:
- [x] Idempotent: re-running updates existing games/results instead of duplicating
- [x] Runs in a single transaction; `--dry-run` rolls back and prints what would change
- [x] **Verification report** after import: per player per season, compare computed net total and games played with the sheet's cached `Итого` and `Кол. игр` values (read with `openpyxl` `data_only=True`). Print mismatches, do not fail.
  - The report also lists games that break the leftover rule (`club.stats.LEFTOVER_MISMATCH`: tour leftover != 0, cash leftover < 0).
- [x] Tests use a small generated `.xlsx` fixture with fake players that mimics the structure, including a bad date and a name alias

## Stage 4. Design system

- [x] Tailwind theme and CSS variables: palette (paper, ink, muted ink, rule lines, stamp red), fonts (self-hosted, Cyrillic support), type scale, spacing
- [x] Base template: header with club name, season switcher (year and kind), footer
  - Year switcher is on the page heading; the kind switch is the header nav (Турниры / Кэш). Кэш, За всё время and Игроки are muted placeholders until Stage 5.
- [x] Components: ledger table (ruled rows, sticky first column, tabular numerals, right-aligned numbers, negative values marked), section heading, stat block
  - Stat block is the italic meta line under the document heading (games, players, buy-ins).
- [x] One reference page: season standings. Built as the real `/<year>/tour/` page over locally imported data (tests use invented players), not a fake-data page. **Stop here for review before Stage 5.**
- [x] Unfold admin themed from the same tokens: ink primary, stamp red only for danger/warning, PT Sans UI, PT Mono numbers, light mode forced (`assets/js/admin-theme.js`), checked in Chromium by `club/tests/test_admin_browser.py`

## Stage 5. Public pages

- [x] `/` redirects to the latest season (tour); an empty site goes to `/all-time/`
- [x] `/<year>/tour/` and `/<year>/cash/`: standings in net order, recent games sidebar, full list of games at `/<year>/<kind>/games/` (linked as "Все игры сезона")
- [x] `/games/<id>/`: game sheet with results, prev/next game of the season, stat strip, balance stamp from `LEFTOVER_MISMATCH`; for cash also the per-player pot (exact chip value minus payout, only when `chips_out` is known) and the chip rate note
- [x] `/players/<slug>/`: record card: stat strip, cumulative net chart (server-side SVG), per-season totals, last 10 games, full paginated history at `/players/<slug>/games/`. A player without games gets the heading only (no 404: admin "view on site" lands here)
- [x] `/players/`: players with at least one game, in name order
- [x] `/all-time/`: standings across all seasons, filter by kind (`?kind=tour|cash`)
- [x] Evening view: when a tour and a cash game share a date, link them to each other
- [x] `noindex` meta and `robots.txt` by default (`SITE_INDEXING` env flag turns both off)
- [x] View tests: status codes, correct numbers on fixture data, no N+1 queries (`django_assert_num_queries`)

- [x] Sortable standings (season and all-time): header links `?sort=&dir=`, a sort row on narrow screens; ИТОГ descending stays the default and the only order with the leader circle.

## Stage 6. Security hardening and deploy

Target: a small KVM VPS (1 vCPU, 1 GB RAM, Ubuntu 24.04), `<domain>`, Docker Compose. Runbook: `docs/deploy.md`.

- [x] Dockerfile (multi-stage, non-root user), `compose.prod.yaml`: `web` (gunicorn, 2 workers), `db` (Postgres 17, `shared_buffers=128MB`), `caddy` (automatic HTTPS, security headers)
- [x] Docker log rotation; server setup notes: swap 1-2 GB, firewall (22/80/443 only), SSH keys only, unattended security upgrades
- [x] Prod settings: HSTS, secure cookies, `ADMIN_URL` from env, CSP, `django-axes`, 2FA for organizers
  - 2FA (TOTP) is built and tested, off by default (`ADMIN_REQUIRE_2FA`); runbook section 10 turns it on.
  - The image refuses to start unless `ENVIRONMENT=production`; unknown `ENVIRONMENT` values fail settings validation.
  - Unfold's untranslated strings: hand-maintained `locale/ru/LC_MESSAGES/django.po`, compiled in the image build.
- [x] Nightly `pg_dump` to off-site storage (e.g. Backblaze B2) with retention; documented and tested restore
  - 14 local dumps, age-encrypted copies in B2 (write-only key, 30-day lifecycle), healthchecks.io dead man's switch.
- [x] Deploy script or GitHub Action over SSH; migrations run on deploy
- [x] Import real data on the server only (runbook section; files deleted from the server afterwards)

Server-side steps (account setup, first deploy, first import) are done by hand from the runbook; tick them there.

## Stage 7. Live game screen

Mobile page for organizers to record a game at the table instead of on paper.

- [x] Start a game (season, date), add players quickly (search + recent players first)
- [x] Per player: +buy-in / rebuy buttons, payout entry; HTMX partial updates, no full reloads
- [x] Tour: elimination order entry, places prefilled from the elimination order, editable; prize payouts prefilled by the season's split (`payout_weights`, `payout_round`)
- [x] Cash: enter final stack `chips_out`, show the stack value in lei using `Season.chips_per_lei`; organizer enters the actual cash payout; show running leftover for the game
- [x] Safe against double taps and lost connection (idempotent requests)
- [x] Only for Organizer group; everything also editable in admin afterwards

## Stage 7b. Blind timer (branch `blind-timer`)

- [x] Blind structure templates in the admin (levels, breaks, one add-on break; sortable), `Season.default_blinds`
- [x] Per-game copy at the start (or attached later), paused until "Пуск"; state in three fields, time computed (`live/clock.py`, mirrored in `assets/js/blind-clock.js`)
- [x] Timer actions through the action engine: pause/resume, next/prev, +1 мин (undoable), level edits, bulk minutes, new levels, new display link
- [x] Phone: timer on the phase strip, add-on break suggested (never applied), structure editor
- [x] Display (табло): organizer page with controls and space bar; secret read-only link; local counting with resync and offset, "нет связи", beeps, Wake Lock
- [x] Stage 1 without a timer: elapsed time, `rebuy_minutes` as a hint only
- [x] `Season.entry_price` default 50

## Stage 8. Club fund (blocked: rules not decided)

The cash leftover goes to a common pot; what happens to it is not confirmed yet. Do not start until this section is updated with the rules.

Planned shape: `fund` app with `FundEntry(date, amount, note, player?, game?)` (signed amounts). Balance = sum of cash leftovers + entries. Covers all expected variants: pot spent on supplies, pot covering someone's buy-in, pot split between players. Historical fund entries below the `Закупка` marker of the cash sheets will be imported as entries.


## Stage 9. Achievements

Ranks, badges, diplomas and titles computed from results. No award is stored: only the rules (ladder steps, thresholds, parameters) live in the database.

Kinds:
- Rank (звание): a step on a ladder over a lifetime counter, with progress to the next step.
- Badge (знак): repeatable, shown with a count.
- Diploma (грамота): awarded once, with a date and the game that earned it.
- Title (титул): one holder per period (ties share it); awarded when the period ends, a leader is shown while it runs.

Definitions live in `CLAUDE.md` ("Achievements") once 9a is done.

### 9a. Rules and calculation (branch `achievements-core`)

- [x] Unknown vs zero: `rebuys`, `addon`, places beyond the paid ones can be unknown on imported games; unknown never counts as zero
- [x] Admin: rebuys, add-on and any place editable on finished games, to backfill history
- [x] Rules in the database: rank ladders with steps, achievement parameters; seeded by a data migration, editable in the admin
- [x] `club/achievements.py`: every award computed from finished games in a fixed number of queries
- [x] `manage.py achievement_stats`: counter distributions and data completeness, to set real thresholds
- [x] Tests for every definition with hand-made fixtures

### 9b. Pages (branch `achievements-pages`)

- [x] `/honors/` (форма № 6, "Доска почёта"): titles by year, badge matrix, rank table, recent awards sidebar
- [x] Player card: "Звания", "Знаки отличия", "Грамоты и титулы" between the format totals and the chart
- [x] Game sheets: badge marks next to names, "По итогам игры присвоено" for ranks and diplomas
- [x] Nav item "Доска почёта" ("Почёт" on narrow screens), query counts, 390px layout in Chromium
