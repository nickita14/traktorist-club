# Backlog

Everything after the stages of `PLAN.md`. One line per item; Russian only where it names a screen or a label.

How it stays true (the rule is in `CLAUDE.md`, "Code conventions"):
- A branch that completes or changes an item updates this file in the same branch: a finished item moves to Done with its branch name, PR and date left empty. New ideas found during the work go to Queue.
- Every branch that edits this file also fills the empty PR numbers and dates in Done: `git fetch`, then `git log --merges --oneline origin/main`, date from the merge commit.
- "In progress" lists only branches that exist.

## In progress

| Item | Branch |
| --- | --- |

## Queue

### Wave 0. Loose ends

- Live start form: only "Турнир" / "Кэш" of the current year (no year in the labels, no older seasons), the date check follows from that; the blind timer field hidden for cash.
- "Бабл-гёрл" column on `/honors/` is empty for everyone: hide a badge column until someone earns it, or add a caption.
- Title rules on `/honors/`: a short visible condition per title, like `badge_rules()` for badges ("Меценат": больше всех оставил на столе за сезон).
- «Меценат» is both a step of the «Кормилец клуба» rank (sum of buy-ins) and the cash season title (most left on the table): rename one of them (the rank step lives in the database, seeded by migration 0011, so this needs a data migration or an admin edit).
- Backup and uptime monitoring: `deploy/backup.sh` and `deploy/sitecheck.sh` ping healthchecks.io once their URLs are set; the checks are not set up yet (or switch to Cronitor or Better Stack).
- Less of the word "лей" across the site: no emphasis on the currency; audit every place it is printed and keep it only where a figure would be ambiguous without it.
- Live screen behavior audit: messages after actions (readable long enough, no stacking, never over buttons), sheets on native `<dialog>` / Popover, keyboard access. No new dependencies.

### Wave 1. End of the 2026 season (deadline: the last game of December)

- "Годовой отчёт за 2026" (форма № 7): a year-in-review page for the club and one per player. Built only on buy-in, payout and places; rebuy and add-on figures only from live-recorded games (unknown is never zero). Mockup first.
- Printable diplomas: a print stylesheet for titles and diplomas, to hand out on paper. No new dependencies. Mockup first.
- Excel export, together with a printable season ledger.

### Wave 2. Redesign "Ведомость 2.0" (mockups first, one branch per item)

- Front page "Стенгазета" instead of the redirect from `/`: the latest evening and its winner, the season leader, fresh awards, a game in progress.
- Palette and paper: try 2-3 token variants on the canvas before touching `tokens.css`.
- Chip preloader: waiting for chip photos; a mockup first.
- Stamp animation: "Баланс сверен" and the winner stamp land on the sheet once; off under `prefers-reduced-motion`.
- Row reorder animation when a table is sorted; animated digits on the blind timer.

### Wave 3. The evening, live

- Blind timer and display (табло) checked at a real table (the live screen itself was checked at a real game in October 2026). Blocks the next item.
- Табло 2.0: bust announcement with the place, a bubble banner when one player is left before the money, bank and players left, a winner screen.
- "Хроника вечера" on the game sheet: a timeline of seats, top-ups, rebuys and exits from the live action log (live games only, public fields only). Mockup first.
- Cash live screen: chips given by the club (a free top-up that does not increase the buy-in), for the rare case when the club gives chips instead of money.
- Leftover check: warn when a finished cash game's leftover is unusually high (threshold in Season), since normal remainders are 0 to 50 lei.
- Manual game entry in the admin (admin-game-entry): two-step add view (season first), result columns by season kind, a balance block.

### Wave 4. Stories

- "Книга рекордов": biggest win, longest in-the-money streak, longest tournament, most crowded evening.
- "Маршрутный лист": where the club has played, how often, who hosted most. Depends on how clean Game place values are: check the data first.
- "Кто кого выбил": an optional tap on a bust in the live screen; feeds a badge and a "nemesis" line. Must stay optional at the table.

## Deferred

- Switching the club to the app from the 2027 season (a season that is not sheet-managed).
- A shareable evening summary for the club chat (link preview).
- "Очная ставка": head-to-head comparison of two players.

## Done

Newest first. Dates are local (Europe/Chisinau), from the merge commits. Dependabot version bumps are not listed.

| Item | Branch | Merged | PR |
| --- | --- | --- | --- |
| Backlog Queue in waves, with the newly agreed items | `backlog-roadmap` | | |
| Cash leftover is a dropped remainder, not a pot: wording, Stage 8 cancelled | `cash-remainder` | 2026-10-27 | #42 |
| Stop tracking local Claude Code settings; deny git writes in `.claude/settings.json` | `claude-settings` | 2026-10-27 | #41 |
| One tracked backlog (this file) | `backlog` | 2026-10-27 | #40 |
| Group codeql-action updates in Dependabot | `dependabot-codeql-group` | 2026-10-27 | #37 |
| Faster browser CI job (no apt installs for Chromium) | `ci-browser-speed` | 2026-10-27 | #36 |
| Skip the test jobs on pull requests that cannot affect them (`changes` job) | `ci-path-filter` | 2026-10-27 | #35 |
| Sheet sync report layout | `sync-report-layout` | 2026-10-24 | #34 |
| Sheet import of chosen dates | `import-date` | 2026-10-24 | #33 |
| Parallel test suite (pytest-xdist) | `test-speed` | 2026-10-19 | #32 |
| Phone nav fixes | `phone-nav-fixes` | 2026-10-19 | #31 |
| Lineup player search (layout and diacritics independent) | `lineup-search` | 2026-10-19 | #30 |
| Player card and history by year | `player-years` | 2026-10-19 | #29 |
| Organizer login link "Проходная" in the header | `header-login` | 2026-10-19 | #28 |
| Live lineup and entry collection ("Состав", "Сбор") | `live-lineup` | 2026-10-18 | #27 |
| Sheet sync from the admin | `sheet-sync` | 2026-10-09 | #20 |
| Badge matrix headers on two lines | `honors-matrix-headers` | 2026-10-01 | #18 |
| Honors board polish | `honors-polish` | 2026-09-30 | #17 |
| Track `CLAUDE.md` and `PLAN.md` in the repository | `track-project-docs` | 2026-09-30 | #15 |
| Fresh base images in CI, weekly image scan, no pip in the runtime image (landed with #15) | `image-refresh` | 2026-09-30 | #14 |
| CI split into jobs, `ubuntu-24.04`, SHA-pinned actions, Dependabot for GitHub Actions, CodeQL (landed with #15) | `ci-split` | 2026-09-30 | #13 |
| Achievements pages ("Доска почёта", player card, game sheets) | `achievements-pages` | 2026-09-30 | #12 |
| Achievements rules and calculation | `achievements-core` | 2026-09-30 | #11 |
| Public-site organizer login ("Проходная") | `login-page` | 2026-09-29 | #10 |
| Page transitions and the blinds bar | `transitions-and-blinds-bar` | 2026-09-28 | #9 |
| Favicon and players list sorting | `favicon-and-players-sort` | 2026-09-28 | #8 |
| Blind timer and display (табло) | `blind-timer` | 2026-09-28 | #6, #7 |
| Automatic deploys from GitHub, with the CI key's forced command | `ci-deploy` | 2026-09-28 | #5 |
| Deploy scripts for CI | `ci-deploy-scripts` | 2026-09-28 | #4 |
