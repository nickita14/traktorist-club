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

- Live start form: only "Турнир" / "Кэш" of the current year (no year in the labels, no older seasons), the date check follows from that; the blind timer field hidden for cash.
- Chip preloader: waiting for chip photos; a mockup first.
- Blind timer and display (табло) checked at a real table (the live screen itself was checked at a real game in October 2026).
- "Бабл-гёрл" column on `/honors/` is empty for everyone: hide a badge column until someone earns it, or add a caption.
- Manual game entry in the admin (admin-game-entry): two-step add view (season first), result columns by season kind, a balance block.
- Backup and uptime monitoring: `deploy/backup.sh` and `deploy/sitecheck.sh` ping healthchecks.io once their URLs are set; the checks are not set up yet (or switch to Cronitor or Better Stack).
- Excel export.

## Deferred

- Row reorder animation when a table is sorted.
- Stage 8, club fund: blocked until the pot rules are decided (`PLAN.md`).

## Done

Newest first. Dates are local (Europe/Chisinau), from the merge commits. Dependabot version bumps are not listed.

| Item | Branch | Merged | PR |
| --- | --- | --- | --- |
| One tracked backlog (this file) | `backlog` | | |
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
