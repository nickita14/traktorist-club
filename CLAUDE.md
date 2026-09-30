# CLAUDE.md

Project rules for Claude Code. Read this file fully before any task. The step-by-step roadmap lives in `PLAN.md`: work on one stage at a time and tick its checkboxes when done.

## What this is

A web app for a friends' poker club ("Кочующий Клуб Тракториста"). It replaces a Google Sheets spreadsheet that tracks tournament and cash games: buy-ins, payouts, places, season standings.

- **Public read access**: anyone with the link can view standings, games, and player pages. No login required.
- **Editing is restricted**: only users in the "Organizer" group (staff) can create or change data, through the Django admin (Unfold) and, later, a mobile "live game" screen.
- **UI language is Russian.** Code, comments, commit messages, and docs are in English.

## Stack

- Python 3.13, dependency management with `uv`
- Django (latest stable, 6.x), PostgreSQL 17
- Admin: `django-unfold`, restyled to match the project palette
- Public pages: Django templates + HTMX. No SPA, no React, no Node build step.
- Styling: Tailwind via `django-tailwind-cli` (standalone binary, no Node), with a custom theme (see Design)
- Tests: `pytest` + `pytest-django`; lint and format: `ruff`
- `pre-commit` with ruff and `gitleaks`
- Local dev: Postgres in `docker compose`; settings from environment variables (`django-environ`)

Do not add new dependencies without saying why in the PR description. Prefer the standard library and Django built-ins.

## Domain model

- **Player**: a real person. `name`, `nickname`, `slug`. One person is one Player across all seasons.
- **Season**: unique pair of `(year, kind)`, where `kind` is `tour` (tournament) or `cash`. Holds rules that may change: `chips_per_lei` (currently 100, i.e. 5000 chips = 50 lei), `paid_places` (currently 3).
- **Game**: one tournament or cash session. `season`, `date`.
- **Result**: one player in one game. `buyin` (total, including rebuys), `payout` (cash actually taken), `place` (nullable, tournaments only), `chips_out` (nullable, cash only: final stack).

### Core principles

1. **Store only source data. Everything derived is computed** with queries/annotations: net result (`payout - buyin`), totals, games played, ITM, count of 1st/2nd/3rd places, standings, game totals, cash leftover.
2. **Cash leftover** (`sum(buyin) - sum(payout)` per game) is never stored. It is rounding change that stays in a common pot. What happens to the pot is not decided yet: do not model it until `PLAN.md` says so. Keep `Game` and `Result` independent from any future fund logic.
3. **Rules live in `Season`**, not in code constants.
4. **`place` is stored explicitly.** An auto-suggestion (rank by payout among positive payouts) may prefill it, but the stored value is the source of truth (ties and historical data need manual control).
5. ITM for a tournament = player finished in the top `paid_places`.
6. Money is integer lei. Use `PositiveIntegerField`, never floats.

## Security rules (public repository)

- **Never commit secrets.** Everything sensitive comes from env vars; keep `.env.example` up to date with dummy values.
- **Never commit real club data**: no real spreadsheets, exports, dumps, or real player names. `data/` is gitignored. Fixtures and tests use invented players.
- Production settings must pass `python manage.py check --deploy` with no warnings.
- Admin lives at a non-default URL taken from env (`ADMIN_URL`).
- 2FA is available behind `ADMIN_REQUIRE_2FA`, off by default; login attempts are rate limited (`django-axes`).
- CSP enabled (Django built-in), no inline scripts beyond what HTMX needs; no third-party CDNs at runtime: vendor static assets. Public pages get a strict policy (`SECURE_CSP`); the admin gets `ADMIN_CSP` (`'unsafe-eval'` for Unfold's Alpine.js, inline styles) from `src/traktorist_club/security.py`. `src/club/tests/test_admin_browser.py` fails on any CSP violation.
- Public pages send `noindex` (meta and `robots.txt`) unless `SITE_INDEXING` explicitly enables indexing.

## Design rules

Direction: **Soviet-era official ledger / factory wall newspaper**. Off-white paper background, dark ink text, one accent color (stamp red), thin ruled lines, dense tables like a sports results sheet. Standings look like an official "ведомость", a player page looks like a personal record card. Light theme only for now.

Fonts (SIL OFL, self-hosted in `assets/fonts/<family>/` with their `OFL.txt`; cyrillic, latin-ext and latin subsets; never a runtime CDN):
- **PT Sans Narrow** 400/700: headings, nav, column headers, labels (`font-head`)
- **PT Serif** 400, 400 italic, 700: body text and player names (`font-body`, the default)
- **PT Mono** 400: every number: money, counts, dates, ranks (`font-num`)
- **Ruslan Display** 400: only the club name in the header (`font-display`)

Tokens (`assets/css/tokens.css`, the only place with raw color values): `paper` #F2EDE3 page background, `ink` #1D1B18 text, `muted` #6B655C nicknames, captions, column headers, `faint` #9A9285 zero values, `rule` #C9C1B2 row lines, `accent` #B3261E stamp red, the only accent, `surface` #FBF9F5 near-white for admin panels and inputs. `frontend/source.css` maps them into the Tailwind theme and removes Tailwind's default palettes and font stacks.

Admin (a work tool that echoes the ledger, it does not copy it): Unfold scales are mixed from the same tokens in `UNFOLD["COLORS"]` (in oklab: oklch loses the hue of near-neutral colors). Primary is **ink**; stamp red only for danger and warning states (`assets/css/admin.css` remaps Unfold's red and orange). Fonts: **PT Sans** 400/700 for the UI, PT Sans Narrow only for page titles (`h1`) and table headers, PT Mono for numbers (display methods in `src/club/admin.py` return `number_cell` / `net_cell` with `formatting="price"`); no PT Serif in the admin. **Light mode only**: `UNFOLD["THEME"]` alone does not win over a theme stored in localStorage, so `assets/js/admin-theme.js` forces it; `src/club/tests/test_admin_browser.py` checks it in Chromium. Unfold ships no translations: set `search_help_text` on every ModelAdmin with `search_fields`.

Number formatting (`src/club/formatting.py`, template library `ledger`): thousands separator U+00A0, narrowed to a thin space by `word-spacing: var(--number-word-spacing)` on number-only elements (`.ledger td.num`, `.num-run`, admin `.admin-num`; never on running text). Not U+202F: the PT fonts lack it and Firefox draws it from a wide fallback font; net with "+", a real minus U+2212 plus accent color for negatives, bare "0" for zero; every zero count (ITM, 1st/2nd/3rd places) is a faint "·" (`{% count_value %}`); a chip stack's value in lei (the cash pot per player) is the one fractional amount, stored nowhere and printed by `format_amount` with a decimal comma only when needed ("2,30", "10"); currency only in column headers ("ИТОГ, ЛЕЙ"). Russian plurals through `ru_plural` / `|plural:"игра,игры,игр"`.

Page transitions: View Transitions API in CSS only (`@view-transition { navigation: auto; types: page }` at the end of `frontend/source.css`): same-origin navigations fade and slide (180ms), htmx board swaps after a button fade (`hx-swap="outerHTML transition:true"`, never on polling); the header, `live-bar` and `bottom-bar` have their own `view-transition-name` (keep them unique on a page, or the transition is skipped). `prefers-reduced-motion: reduce` turns them off; browsers without the API navigate as before. Tested in `src/club/tests/test_browser_transitions.py`.

Components live in `frontend/source.css` (`@layer components`): `site-nav`, `doc-head` (label, title, `stamp`, meta line, closed by a 3px double rule; pages extend `src/templates/club/document.html`), `stamp-ink` (a plain fact; red `stamp` is for alerts), `doc-pager` (prev/next links above a heading, page links under a table), `link-box` (bordered cross-reference), `stat-strip` (ruled figure cells, a `<dl>`; `stat-strip-2` / `stat-strip-3` fix the cell count at every width, use them when a strip has 2 or 3 cells), `segmented` (year switcher; `segmented-words` for word labels), `ledger` table (sortable headers on the standings and `/players/`: `sort-link` with a CSS-drawn arrow, `sort-links` row on narrow screens, parsing in `src/club/sorting.py` where each table is a `Table` (columns, default column, text columns that start from A), template library `sorting`) (plus `ledger-compact` for sidebars, total row in `tfoot` under a 3px double rule), `rank-leader`, `page-grid` (content plus a 330px sidebar), `net-chart` (server-side SVG, geometry in `src/club/charts.py`, wrapped in `{% localize off %}`), `section-head` (h2 plus a note or control on the right, closed by a 3px double rule; the award sections), `nav-long` / `nav-short` (the nav label on phones: "Почёт"; five items fit on one line at 390px), awards in ink only (no accent): `award-grid` / `award-stamp` (`award-stamp-none` when not earned; tilt per badge through `[data-badge]` and `--award-tilt`), `award-mark` (the badge tag at a name on a game sheet, half the tilt, keeps the row height), `award-kind` (ЗНАК, ЗВАНИЕ, ГРАМОТА, ТИТУЛ, ИДЁТ), `award-note`, `award-feed` (sidebar list), `rank-cells` / `rank-progress` (a `<progress>`, 8px, ink on `surface`) / `rank-steps` / `rank-roman`. `.ledger-scroll` is `position: relative` so `.sr-only` labels in far cells are clipped with the table instead of widening the page. The tournament game sheet lists results by place (no-place rows after, by net) with МЕСТО as its first column; the cash sheet lists by net with a № column. The reference page is `/<year>/tour/` (title "Ведомость турниров", year in the switcher and the meta line). A player is identified by `Player.label` (nickname when there is one, otherwise name) where one word fits, and by `club/includes/player_name.html` (name plus muted nickname, linked) where there is room. A missing value (cash place, unknown chip stack) is a faint "·" (`{% no_value %}`, `{% optional_count %}`, `{% pot_value %}`).

Hard bans (these are what make projects look generated):
- no gradients, glassmorphism, glow, or large drop shadows
- no rounded "card grid" layouts as the default way to show data; data goes in tables
- no purple/indigo/blue default Tailwind palettes; use only the project tokens
- no emoji as icons; no icon unless it carries meaning
- no Inter, no system-default-looking font stack as the main face (use the fonts above)
- no hero sections, no marketing copy

Implementation:
- All colors, fonts, spacing live in one place (Tailwind theme + CSS variables). Never hardcode hex values in templates.
- Numbers are right-aligned; negative values use the accent color or a clear minus, not only color.
- Mobile first: tables scroll horizontally inside their own container, the page never scrolls sideways.
- Before introducing a new visual pattern, check how an existing page solves it and reuse that. If nothing fits, stop and propose the pattern instead of inventing silently.

## Live game screens

Organizer phone screens at `/<ADMIN_URL>live/` (`live` app, templates in `src/templates/live/`), mounted before the admin's catch-all.
- Access: `live.access.organizer_required` (superuser or Organizer group; a verified session when `ADMIN_REQUIRE_2FA`); anonymous users go to the admin login. Every view keeps the strict `SECURE_CSP`, not `ADMIN_CSP`.
- A live game has a non-empty `Game.live_stage` (`rebuys`, `addon`, `final`, `cash`) and counts nowhere (stats, public pages, leftover check) until it is empty again. `Result.rebuys`, `addon`, `out_order` are filled only by live games; places come from `out_order` (`stats.elimination_places`) and are stored when results are saved.
- Every change goes through `src/live/actions.py`: one transaction, game row locked, `F()` updates, logged as a `LiveAction` whose unique `key` (rendered per button) makes requests idempotent and whose before/after snapshots make undo exact. The log is deleted with its game (CASCADE); deleting a game is recorded in Django's `LogEntry` (the admin writes one, "ОТМЕНИТЬ ИГРУ" too). Never change results from a live view any other way.
- htmx 2 is vendored in `assets/js/vendor/` (byte for byte, excluded from whitespace hooks) and configured by the `htmx-config` meta (no eval, no injected styles). `assets/js/live.js` does the error banner and retry, pauses polling while typing, quick amounts and the sheet. No inline scripts or `style=` (use `<progress>` for bars).
- Tournament results screen: prize payouts are prefilled by `stats.split_prizes` from `Season.payout_weights` (one per paid place, validated in `Season.clean`) and `payout_round` (round down, remainder to 1st); "Распределить заново" recomputes the prize rows and clears every other row (non-prize places are paid 0).
- Number inputs: `type="number"` with `inputmode="numeric"`; `.live-input` hides the spin buttons. The structure editor's number columns fit 5-digit blinds at 390px (`src/live/tests/test_browser_layout.py`).
- The structure page ends with a "Добавить" block (both add forms; inputs and buttons join their form through the `form` attribute, which htmx posts as `form.elements`), a "Табло" section ("Новая ссылка" asks first with `hx-confirm`) and a sticky `bottom-bar`: "← К игре" and "Поделиться табло" (`navigator.share`, else the link is copied and a toast says so; `assets/js/live.js`).
- Organizer shortcuts on public pages (`live.context_processors.organizer`, lazy): "Живая игра" and "Админка" after the nav, a `live-strip` per game in progress, and the manifest link. Only for superusers and the Organizer group: nothing that reveals ADMIN_URL may render for anyone else (`src/live/tests/test_shortcuts.py`). Anonymous pages pay no query; a signed-in user pays the session, the user and one shortcuts query.
- The web app manifest (`live:manifest`, start_url the live index) is a 404 for everyone but organizers; its colors come from tokens.css (`src/live/manifest.py`).
- The tab icon is for everyone: `templates/includes/favicons.html` (SVG, 32 px PNG, apple-touch-icon; static files only) in `base.html`, `live/base.html` and `live/tablo.html`, `UNFOLD["SITE_FAVICONS"]` in the admin; `/favicon.ico` redirects to the static `.ico`.
- Stage 1 without a blind timer shows elapsed time ("идёт 1:17"); `Season.rebuy_minutes` is only a hint ("обычно в это время закрывают ребаи", muted, never accent). The organizer always changes the stage.
- Live components in `frontend/source.css`: `live-bar`, `phase-strip`, `live-row`, `btn` (44px minimum; `btn-accent`, `btn-on`, `btn-primary`, `btn-link`), `bottom-bar`, `sheet-panel`, `toast`, `conn-error`, `timer-controls`, `blinds-row`, `tablo-*`. `src/live/tests/test_browser.py` checks touch targets, CSP and the flows in Chromium.

## Organizer login

- `/prokhodnaya/` ("Проходная", `club.auth`, template `club/login.html`): the public-site login, beside the admin one (which stays). `OrganizerLoginForm` subclasses the admin `LoginForm`, so it runs the same backends (django-axes counts failures and lockouts across both pages), the TOTP code with `ADMIN_REQUIRE_2FA`, and CSRF. It drops the admin's `is_staff` check for an organizer check.
- Non-organizers, wrong passwords and unknown users all get the same `REFUSED` message; a right password for a non-organizer is sent as `user_login_failed`, so neither the message nor the lockout tells them apart.
- `next` goes through `club.auth.safe_next` (same host, never under ADMIN_URL, never the login pages); without one the page the visitor came from (Referer), else home. The page never contains ADMIN_URL; noindex meta, `X-Robots-Tag`, `Disallow` in robots.txt.
- "Войти" is a quiet link in the footer for anonymous visitors (`footer_login` block, empty on the login page); organizers get "Выйти" (a POST form, `logout`) after the shortcuts in the nav. Tests: `src/club/tests/test_login.py`, `test_login_browser.py`.

## Blind timer

- Templates: `club.BlindStructure` + `BlindLevel` (admin, sortable Unfold inline; positions renumbered 1..n on save, rows without a position go last). Each admin row has a "Тип" (`BlindLevelForm.kind`: Уровень/Перерыв, not stored: a break is a row without blinds); `assets/js/admin-blinds.js` hides and disables the other type's inputs and keeps the "Ур." numbers (breaks skipped) right while dragging. "Ур." is the first column because Unfold draws the drag handle in the first column. Unfold's `conditional_fields` do not work in inlines (0.108). `Season.default_blinds` is preselected at start ("Как в сезоне"). A level has blinds (`1 <= small <= big`, optional ante); a break has a label and no blinds; at most one `addon_break`.
- A tournament gets its own copy (`live.BlindTimer` + `TimerLevel`, no FK to the template), paused at level 1 until "Пуск". The timer stores only `position`, `started_at`, `paused_at`: `live/clock.py` (`clock_at`) walks the levels' minutes from there, so levels change without writes. Resume and "+1 мин" move `started_at`; every change goes through `live/actions.py` (idempotent keys, logged; pause/resume/next/prev/+1 undoable; level edits logged, not undoable). Timer actions do not block undoing a stage change.
- The phone editor adds rows with "+ Уровень" and "+ Перерыв" (`actions.add_break`; the add-on checkbox only while the game has no add-on break).
- Edits: played rows locked; the current level's minutes never below the time played; its blinds only while paused or in its first minute. The last level stops at 0:00 ("уровни закончились · добавьте уровень"); a level added then starts fresh.
- `assets/js/blind-clock.js` mirrors `clock_at`; `src/live/tests/clock_cases.json` runs through both (pytest and Chromium). Keep them in step.
- Display: organizer page `<ADMIN_URL>live/<pk>/tablo/` (controls, space bar) and the secret link `/tablo/<token>/` (no login, no controls, standalone template: never the site nav, shortcuts or manifest, so no ADMIN_URL; `X-Robots-Tag`, `Disallow: /tablo/`). A new token revokes the old link; finished games 404. `assets/js/tablo.js` counts locally, resyncs every `TIMER_SYNC_SECONDS` with a clock offset, shows "нет связи" when a sync fails, beeps (Web Audio) and holds a Wake Lock.
- The phone strip shows the timer when there is one; at the add-on break it suggests "Перерыв на аддон" but never changes the stage.

## Achievements

Ranks, badges, diplomas and titles, all computed by `src/club/achievements.py`; no award is stored. Only the rules are in the database: `RankLadder` (fixed codes `veteran`, `feeder`, `addon`; never added or deleted) with ordered `RankStep`s (threshold, title), and `AchievementSettings` (one row, pk 1: no-skip minimum evenings, always-ITM minimum tournaments, hat-trick length, comeback extra buys, ITM and evening series steps as "5,10"). Seeded by migration `0011`, editable in the admin by organizers.

- Only finished games count (empty `live_stage`). Order: date, tournament before cash on the same date. A club evening is a date with at least one finished game; a player attended it by playing any game that date. ITM = `place <= paid_places`. Ties share every award.
- Unknown never counts: NULL `addon` is not an add-on, NULL `place` is neither ITM nor a bubble, NULL `chips_out` leaves the result out of the pot. Imported games have NULL `rebuys`, `addon`, `out_order` and places only for paid players; organizers backfill them in the Game inline (places beyond the paid ones allowed, payout 0). `stats.buyin_warning` warns (never blocks) when a buy-in differs from entry + rebuys + add-on at the season's prices. `import_sheet` keeps a backfilled place above `paid_places` while the sheet pays that player nothing.
- Ranks: lifetime counters (finished games; sum of buy-ins; known add-ons), the current step, the next one and the game where each step was reached. Below the first threshold: no rank.
- Badges (repeatable, with the game): `bubble` (tour, `place == paid_places + 1`; two sharing 3rd leave no place 4, so no bubble), `cashier` (cash, best net, > 0), `one_buyin` (tour 1st, `buyin == entry_price`), `comeback` (tour ITM, `buyin >= entry + N * rebuy_price`), `hat_trick` (ITM in N of the player's tournaments in a row; skipped tournaments do not break it; one per streak), `no_skip` ("Ни одного прогула": every club evening of a calendar month with at least the minimum; only once the month has ended, no running state; one per player per month, dated on the month's last day, linked to the player's last game that month, basis "4 из 4 вечеров, август 2026").
- Diplomas (once each, with the game): `first_win`; `itm_series_<n>` (same streak rule); `evening_series_<n>` (consecutive club evenings, a missed one breaks it; linked to the player's first game that evening).
- Titles (`TitleResult`, dated on the period's last day, awarded once `today` is past it; while it runs, `running` with the current leaders): `udarnik` (most evenings in a meteorological season; winter "Зима 2025/26" filed under the year it ends), `always_itm` (tour season, ITM in every tournament, at least the minimum of them, also while the season runs), `patron` (cash season, largest sum of chip value minus payout as an exact `Fraction`, known stacks only). Only `udarnik` and `patron` are contests (`TitleResult.contest`): several holders of an ended one are a tie ("ничья"), and a running one's note is "лидирует"; running `always_itm` holders are "пока без промахов" (`running_note`). Empty `holders` means "претендентов нет"; `no_data` (patron only) means no known `chips_out` in the season (the importer never sets it, so imported cash seasons have none): the page shows "нет данных о фишках".
- API: `achievements.load(today=None)` returns `ClubAwards` in `QUERY_COUNT` (4) queries whatever the data size (settings, steps, finished results, players); its methods run none: `player(pk)` (ranks with progress, badge counts with the last award, diplomas, titles, running titles led), `game(pk)` (badges per player, ranks and diplomas reached), `honors(year)` (titles filed under the year; badge matrix and rank table all-time), `title_years()`, `recent(limit)`, `counters()`, `completeness()`. `today` defaults to `timezone.localdate()`; tests pin it. Without the settings row the field defaults apply and nothing is written; without ladders there are no ranks.
- Display facts come from the module too, never from views or templates: `GameRef.number` / `.document` ("Турнир № 34", "Вечер № 12", the site's numbering via `stats.game_number` in the same query), `Award.basis` (threshold, streak start, title figure), `Award.player`, `Award.kind_label`, `TitleResult.holder_values` / `holder_metric(pk)` / `shared_metric` (None when holders' figures differ: each holder then shows their own) / `lead_text(pk)`, `Ladder.counter_label` / `unit_forms`, `RankProgress.step_number` / `step_states`, `PlayerAwards.badge_slots` / `record` / `lead_lines` / `veteran`, `ClubAwards.badge_rules()`, `honor_years()` (years with finished games or ended titles), `latest_game_year()`, `honors(year, running=...)` (`title_rows`, `badge_rows`, totals), `sort_badge_rows()`. Template helpers: `ledger|roman`, `{% load awards %}{% award_url award %}`.
- Rule and basis texts (`badge_rules()`, `Award.basis`, `lead_text`) must say exactly what the definition computes: `one_buyin` and `comeback` read the buy-in, so they speak of "докупки" (a rebuy or an add-on), never "ребаи"; streaks over the player's own tournaments say "своих турнирах". Change a definition and its texts (and their tests) together.
- Pages: `/honors/` (`views.honors`, "Доска почёта", форма № 6): titles by `?year=` (default the latest year with games plus every running title; any year not in `honor_years()` is a 404), the badge matrix (sortable, `sorting.BADGES`), the rank table, the last `RECENT_AWARDS` awards. The player card shows ranks, badges and the record between the stat strip and the chart (the doc-head stamp becomes the veteran step when there is one); game sheets mark badges at the names and list "По итогам игры присвоено". Query budget: `/honors/` 1 + `QUERY_COUNT`, player card and game sheet 5 + `QUERY_COUNT` (a player without games does not load awards). Tests: `test_views_honors.py`, `TestPlayerAwards`, `TestGameAwards`, `test_awards_browser.py` (390px); view tests use the `award_rules` fixture (`club/tests/conftest.py`).
- `manage.py achievement_stats` prints the counters (per player, quartiles, players per rung) and data completeness, to set real thresholds (feeder and add-on steps are provisional).
- Tests: `src/club/tests/test_achievements.py` (every definition, query count), `test_achievement_stats.py`, `test_migrations.py` (the seed forward and backward, a transactional test: it runs after the others and flushes the seed, so tests that need rules create them).

## Unfold reference

Unfold changes often. Do not rely on memory for its API or settings.
Before writing or changing any admin code, fetch the current docs and check
the installed version (`uv pip show django-unfold`) against the changelog.

- Docs: https://unfoldadmin.com/docs/ (start at installation/quickstart,
  configuration/settings, configuration/modeladmin, inlines, filters,
  styles-scripts/customizing-tailwind)
- Library source: https://github.com/unfoldadmin/django-unfold
- Primary reference for patterns: the docs above plus the installed package
  source (`.venv/lib/python3.13/site-packages/unfold/`), which always matches
  the locked version. The former demo repo (`unfoldadmin/formula`) is gone
  (404, not in the org as of 2026-09); do not rely on it.
- Turbo (https://github.com/unfoldadmin/turbo): Django + Next.js boilerplate.
  Use only its Django admin configuration as reference, never its frontend
  or project structure.
- Live demo: https://demo.unfoldadmin.com

Rules:
- Unfold Studio is a paid plugin. Never add it as a dependency. Theme
  customization is done only with free options (`UNFOLD` settings, colors,
  custom stylesheet).
- All admin classes inherit from `unfold.admin.ModelAdmin` and Unfold inlines.
- Admin palette and fonts come from the same design tokens as public pages.

## Code conventions

- Apps: `club` (players, seasons, games, results, stats), `importer` (spreadsheet import), `live` (live game screens and their action log), later `fund`.
- Layout: the Django code (`traktorist_club`, the apps, `templates/`, `locale/`) lives in `src/`; `manage.py`, `conftest.py`, `assets/`, `frontend/`, `deploy/` and `docs/` stay at the root. Packages keep their top-level names: `src/` is put on the path by `manage.py`, pytest (`pythonpath`), ruff (`src`) and the image (`PYTHONPATH=/app/src`), never by renaming imports. `BASE_DIR` is the repository root, `SRC_DIR` is `src/`.
- Put stats logic in one module (e.g. `src/club/stats.py`) as queryset helpers; views and admin call it, never duplicate aggregation.
- Every aggregate (total, ITM, places, leftover) has tests with small hand-made fixtures.
- Small, focused commits. Commit message style: short imperative subject in English.
- Commit messages have no `Co-Authored-By` or any other trailers. This applies to every git commit command proposed in the "Commits" section, including patch-based ones (`git apply --cached ... && git commit`).
- Never run `git add`, `git commit` or `git push`. The user commits by hand. When a task is done, end the report with a "Commits" section: for each proposed commit, the exact file list and the commit message (short imperative subject in English, optional body). Each commit must leave tests passing. Also give the ready-to-paste commands, for example:
  `git add path/a.py path/b.py && git commit -m "Add player slug generation"`
- All work happens on a branch, never directly on `main`. Branch names are short, lowercase, hyphenated: `ci-deploy`, `fix-sorting`. The "Commits" section starts with the branch command (`git switch -c <name>`, or `git switch <name>` if it exists) and ends with `git push -u origin <name>`. The user opens and merges the pull request.
- Merging to `main` deploys to production automatically (`deploy` job in `.github/workflows/ci.yml`), so a branch is ready only when the full checklist passes:
  1. `uv run ruff check . && uv run ruff format --check .`
  2. `uv run pre-commit run --all-files`
  3. `uv run pytest` with Chromium installed, `tailwind build` done and messages compiled (no browser or translation test skipped; CI sets `REQUIRE_BROWSER_TESTS` and `REQUIRE_COMPILED_MESSAGES`). CI runs it as two jobs, `-m "not browser"` and `-m browser`; locally the plain run covers both
  4. `manage.py check --deploy --fail-level WARNING` with production settings, as in CI (which runs it inside the built image)
  5. `docker build --pull` of the production image, when the Dockerfile, dependencies, `deploy/` or the static assets changed
  6. new migrations reviewed: they run on the next deploy (a pre-deploy dump is taken first)
- Never propose rewriting history (fixup, rebase, amend) for commits that are already pushed. Before suggesting a rebase, run `git fetch` and `git status -sb` and check that the target commits are not on origin. If they are, propose ordinary new commits instead.
- **Never use the em dash character (U+2014) anywhere**: code, comments, docstrings, templates, docs, commit messages, PR descriptions. Use a comma, colon, parentheses, or a hyphen. En dashes are fine.

## CI

Three workflows. `.github/workflows/ci.yml` runs on pushes to `main` and on pull requests; its jobs run in parallel, so a red job names the kind of problem. `.github/workflows/codeql.yml` and `.github/workflows/image-scan.yml` run on the same events and weekly.

Required (status checks in the `main` ruleset, and `deploy` waits for all of them):
- `lint`: ruff check, ruff format --check, `pre-commit run --all-files`. No database.
- `unit`: `pytest -m "not browser"` on Postgres 17 with compiled messages (`REQUIRE_COMPILED_MESSAGES`), under `coverage` (config in `pyproject.toml`); uploads `coverage.xml` (artifact `coverage`) for sonar. First it checks that unit + browser collect exactly the whole suite. No Chromium, no Tailwind.
- `browser`: `pytest -m browser` on Postgres 17 after `tailwind build`, Chromium cached by Playwright version (`REQUIRE_BROWSER_TESTS`). On failure it uploads `test-results/playwright/` (artifact `playwright-failures`): a screenshot per page and a trace per context (`uv run playwright show-trace <zip>`), written by the root `conftest.py` locally too.
- `security`: gitleaks over the full history (same release as the pre-commit hook; bump both, and the checksum, together), pip-audit over `uv export --locked --all-groups` (hashes, no resolving), zizmor over the workflows and `dependabot.yml`. No database.
- `image`: `docker build --pull` of the production image, the refusal to start with `ENVIRONMENT=development`, no pip in the base interpreter (`--entrypoint /usr/local/bin/python`: the venv's `python` never sees `/usr/local` packages), and `check --deploy --fail-level WARNING` run inside the image with production settings. One-off commands against the image bypass the entrypoint (`--entrypoint python`); only the dev-mode refusal goes through it, because that is what it tests.

Advisory (never required, never block `deploy`):
- `image-scan` (own workflow, `.github/workflows/image-scan.yml`: push to main, PRs, weekly, manual): builds the image with `--pull` and runs Trivy (fixable HIGH/CRITICAL only). Base-image CVEs arrive on their own schedule; a red run means refresh the base (`deploy/deploy.sh` once `python:3.13-slim` has the fix) or bump a package soon, not stop the line. No `apt-get upgrade` in the Dockerfile: if a Debian finding with a fix stays open more than a week after a base refresh, revisit that.
- `sonar`: SonarQube Cloud (`sonar-project.properties`, org `nickita14`) with the unit coverage; tests under `src/**/tests/**` are analyzed as tests (out of coverage and duplication). Skips green with a notice when `SONAR_TOKEN` is unavailable (forks, Dependabot).
- `codeql (python)`, `codeql (actions)`: code scanning results in the Security tab.
- `deploy` (not a check to require: it only runs on `main`): unchanged, see `docs/deploy.md` section 12.

Test split: the root `conftest.py` marks every test that uses the `browser` fixture as `browser` (by the fixture, not the file name; never add the marker by hand). A test that needs Chromium must use that fixture.

pip-audit exceptions: when an advisory does not apply and no fixed version can be locked yet, add one line to the `ignored=(...)` array in the "pip-audit" step: `--ignore-vuln <ID>  # why it does not apply; review by YYYY-MM-DD`. Remove it once a fix is locked or on its review date. Nothing else silences pip-audit.

Workflow rules: `runs-on: ubuntu-24.04` (the server's release); every action pinned by full commit SHA with its version in a comment (Dependabot updates both, after a 7-day cooldown); `permissions: contents: read` at the workflow level, more only on the job that needs it; `persist-credentials: false` on every checkout; secrets only through `env`. Tools that run only in CI (pip-audit, zizmor, Trivy, Sonar, CodeQL, gitleaks) are actions or `uvx`, never project dependencies. Check a workflow change locally with `uvx zizmor .` (add `GH_TOKEN=$(gh auth token)` for the online audits) and `uvx --from actionlint-py actionlint`.

## Commands

```bash
uv sync                                  # install
docker compose up -d db                  # local Postgres
uv run python manage.py migrate
uv run python manage.py tailwind runserver   # dev server with Tailwind watcher
uv run python manage.py tailwind build       # one-off CSS build (assets/css/tailwind.css, gitignored; browser tests fail without it, CI builds it)
uv run python manage.py compilemessages --ignore=.venv   # Unfold's Russian strings (needs gettext; .mo gitignored)
uv run pytest
uv run playwright install chromium      # once, for the browser tests (they skip without it)
uv run playwright install-deps chromium # once on Linux if Chromium lacks system libraries (sudo)
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files
uv run python manage.py list_sheet_names data/<file>.xlsx          # raw names, for data/aliases.yaml
uv run python manage.py import_sheet data/<file>.xlsx --aliases data/aliases.yaml --dry-run
uv run python manage.py totp_enroll <username>   # admin 2FA device (QR code in the terminal)
uv run python manage.py achievement_stats        # rank counters and data completeness (stdout only)
uv run python manage.py render_icons             # home screen icons and favicons (assets/icons/) from the tokens; needs Chromium

# Production (docs/deploy.md is the runbook)
docker build --pull -t traktorist-web . # the production image, as the server builds it (fresh base)
                                        # pushes to main deploy themselves (ci.yml "deploy" job, runbook section 12)
deploy/deploy.sh [ref]                  # from your machine: deploy origin/main (or ref) over SSH; rollbacks
deploy/smoke-check.sh <domain>          # HTTPS 200, HSTS, HTTP redirect (run by deploy.sh and the CI job)
                                        # deploy/ci-deploy.sh: the CI key's forced command (a main SHA only)
deploy/backup.sh [--local-only]         # on the server: dump, verify, rotate, off-site, ping
deploy/restore.sh [--replace] <dump>    # on the server: restore drill, or replace the live DB
```

Keep this section accurate when commands change.
