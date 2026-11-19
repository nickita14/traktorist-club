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

- Gender-neutral award texts: the badge name "Снял кассу" (printed at a player's name on game sheets and the player card) and the «Меценат» rule "больше всех оставил на столе" assume a man; Player has no gender, so reword them impersonally (as "оставлено 12,30" already is). "Бабл-гёрл" is a deliberate joke name: decide whether it stays.
- shellcheck for `deploy/*.sh` in pre-commit or CI (a one-off run is clean; today nothing runs it).
- Live screen behavior audit: messages after actions (readable long enough, no stacking, never over buttons), sheets on native `<dialog>` / Popover, keyboard access. No new dependencies.

### Wave 1. End of the 2026 season (deadline: the last game of December)

- Printable diplomas: a print stylesheet for titles and diplomas, to hand out on paper. No new dependencies. Mockup first.
- Excel export, together with a printable season ledger.
- Year report: the shared-rank line names the others in the nominative ("2–3 место · наравне: Марина") because `Player.label` cannot be declined; the mockup's "вместе с Мариной" needs a stored instrumental form per player (or another wording). Decide before the first January release.

### Wave 2. Redesign "Ведомость 2.0" (mockups first, one branch per item)

- Front page "Стенгазета" instead of the redirect from `/`: the latest evening and its winner, the season leader, fresh awards, a game in progress. Chosen direction: a hybrid (the newspaper masthead and headline, the "Гонка сезона" chart as the one hero, stamps and the chip as details).
- Palette and paper: try 2-3 token variants on the canvas before touching `tokens.css`.
- Chip preloader: waiting for chip photos; a mockup first.
- Stamp animation: "Баланс сверен" and the winner stamp land on the sheet once; off under `prefers-reduced-motion`.
- Row reorder animation when a table is sorted; animated digits on the blind timer.

### Wave 3. The evening, live

- Theme nights, next steps found while building them: an add-on announcement (ADDON_ON is not an event yet), a per-theme accent color for the presets, and a sound for the level change in place of the beep.
- Blind timer and display (табло) checked at a real table (the live screen itself was checked at a real game in October 2026); the first theme evening is that check.
- Табло 2.0: bust announcement with the place, a bubble banner when one player is left before the money, bank and players left, a winner screen. First slice (bust with the place, bubble burst, winner, themes) done in `theme-nights`; left: the bubble banner while one player is left before the money, bank and players left.
- "Итоги вечера": a phone screen right after a game ends (dark table, a falling chip, the winner large, places appearing one by one, a badge stamp). Poster style per `CLAUDE.md` "Celebration pages"; it doubles as the final screen of Табло 2.0.
- Live cash game closed the next day: the 2026 audit found cash games finished about 20 hours after the start, so "ЗАВЕРШИТЬ" was pressed the next afternoon. Any duration figure must not trust the finish time of a cash game; consider a reminder on the live index for a game left open overnight.
- "Хроника вечера" on the game sheet: a timeline of seats, top-ups, rebuys and exits from the live action log (live games only, public fields only). Mockup first.
- Cash live screen: chips given by the club (a free top-up that does not increase the buy-in), for the rare case when the club gives chips instead of money.
- Leftover check: warn when a finished cash game's leftover is unusually high (threshold in Season), since normal remainders are 0 to 50 lei.
- Manual game entry in the admin (admin-game-entry): two-step add view (season first), result columns by season kind, a balance block.

### Wave 4. Stories

- "Книга рекордов": biggest win, longest in-the-money streak, longest tournament, most crowded evening. Longest tournament only from live games (the 2026 audit found start and end times only there).
- "Маршрутный лист": where the club has played, how often, who hosted most. Blocked on data: the 2026 audit found `Game.location` empty on every game, live ones included, and no link from a game to a host player.
- "Кто кого выбил": an optional tap on a bust in the live screen; feeds a badge and a "nemesis" line. Must stay optional at the table.

## Deferred

- Switching the club to the app from the 2027 season (a season that is not sheet-managed).
- A shareable evening summary for the club chat (link preview).
- "Очная ставка": head-to-head comparison of two players.

## Done

Newest first. Dates are local (Europe/Chisinau), from the merge commits. Dependabot version bumps are not listed.

| Item | Branch | Merged | PR |
| --- | --- | --- | --- |
| Debian security fixes in the runtime image without waiting for the base: `apt-get upgrade` in the runtime stage (an unreachable mirror fails the build), its cached layer expired daily on the server by `APT_REFRESH`, a test that a failed build stops the deploy; expired `.trivyignore` entries removed | `image-apt-upgrade` | | |
| Theme nights (Тематика) on the display (табло): admin themes with captions, overlay presets and uploaded sounds (stored in the database); bust with the place, prize-place bust, bubble burst, winner, rebuy and the break screen with tips, from the action log; the club's own stamp when no theme is active; a preview page (the first slice of Табло 2.0) | `theme-nights` | 2026-11-20 | #63 |
| Season 2027 alongside the sheet: a new season (live screen, import, an organizer in the admin) copies the rules and "Ведётся в таблице" from the previous year's season of its kind; a season without a finished game is kept out of `/`, the header nav and the year switchers; `Season.sheet_title` maps a tab with any name to its season (superusers) | `season-2027` | 2026-11-19 | #61 |
| "Годовой отчёт за 2026" (форма № 7): club and player pages in the poster style, the release (organizers close the year in "Годовые отчёты", 1 January fallback, preliminary pages for organizers only), `club.YearClosure`, links from the card, `/honors/` and the standings | `year-report-pages` | 2026-11-16 | #60 |
| Data check for the year report on production data: which year-report figures 2026 data can produce; result recorded in `PLAN.md` Stage 10 (duration, «Меценат» and hosts dropped; bubble only where every place is known) | `year-report` | 2026-11-16 | #59 |
| Design decisions in the docs: "Celebration pages" in `CLAUDE.md` (poster style, `poster-red`, motion) and the year report, front page and "Итоги вечера" items | `design-decisions` | 2026-11-16 | #58 |
| Backup and uptime monitoring on Cronitor heartbeats: `MONITOR_BACKUP_URL` / `MONITOR_SITE_URL` (renamed from `HEALTHCHECKS_*`), failures as `state=fail` through `deploy/monitor-ping.sh`, the URL never printed; runbook for the monitors and the server switch | `monitoring` | 2026-11-15 | #57 |
| No currency word on the public site and the live screens ("Итог", "закупки на 15 020", "закупок на 7 400", "оставлено 12,30", "закупка 50 = 5 000 фишек", "Вход 50 не сдан."); the admin keeps units; a test renders every club and live template and fails on the word | `less-lei` | 2026-11-15 | #56 |
| `/honors/`: a badge gets a matrix column only once someone has earned it; every badge keeps its rule, an unearned one noted "ещё никем не получен" | `honors-fixes` | 2026-11-15 | #55 |
| `/honors/`: a short rule per title (`title_rules()`), figures from the settings | `honors-fixes` | 2026-11-15 | #55 |
| Live start form: "Турнир" / "Кэш" only, the season is the date's year of that kind (created with the previous rules if missing); a date outside today's year asks first; the blind timer field hidden for cash and ignored by the server | `live-start-form` | 2026-11-15 | #54 |
| Backlog Queue in waves, with the newly agreed items | `backlog-roadmap` | 2026-11-15 | #53 |
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
