"""The sheet sync page: pull the club Google Sheet, preview the import, apply what was previewed.

Superusers only: the section is hidden from everyone else and the page answers 403 to organizers
(anonymous visitors go to the admin login). The flow:

1. "Подтянуть из таблицы" fetches the workbook (importer.sources) into a SheetSnapshot, deleting
   any older one, and shows the dry-run report (importer.service) of the chosen sheets and dates
   ("Пересчитать" changes them; no date ticked means every date).
2. "Применить" imports the stored bytes, never a new fetch, and only while the import makes
   exactly the changes the page showed: the report's digest travels with the form, and a
   different result (the database changed meanwhile) is rolled back. The snapshot is deleted
   and the apply is logged (LogEntry).

Snapshots older than SheetSnapshot.TTL are refused and purged on every request to the page.
"""

import datetime
import hashlib
from dataclasses import dataclass, field

from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.db import transaction
from django.shortcuts import redirect
from django.urls import path, reverse
from django.views.generic import TemplateView
from unfold.admin import ModelAdmin
from unfold.views import UnfoldModelAdminViewMixin

from club.achievements import MONTHS
from club.formatting import format_money, format_net, ru_plural
from club.models import SeasonKind
from importer import sources
from importer.models import SheetSnapshot
from importer.report import (
    CREATED,
    DELETED,
    LIVE_IN_PROGRESS,
    LIVE_RECORDED,
    ImportReport,
    SheetReport,
)
from importer.service import ReportChanged, UnknownDates, found_dates, run_import
from importer.verify import GAP, MISMATCH, TIE, Leftover
from importer.workbook import SheetError, import_sheet_titles, parse_workbook

SYNC_URL_NAME = "importer_sheetsnapshot_sync"

FIELD_LABELS = {"buyin": "закупка", "payout": "выплата"}
PLACE_LABEL = "место"
CREATED_LABEL = "новый результат"
DELETED_LABEL = "результат удалён"
CHANGE_ORDER = [PLACE_LABEL, *FIELD_LABELS.values(), CREATED_LABEL, DELETED_LABEL]
PLACES_NOTE = "Места считаются по выплатам из таблицы. Исправленное вручную место будет заменено."
SKIPPED_LABELS = {
    LIVE_RECORDED: "записана на живом экране",
    LIVE_IN_PROGRESS: "идёт живая игра",
}
# Verification groups in the order the page shows them: (label, group title, alert).
FINDING_GROUPS = [
    (TIE, "Ожидаемо: ничья (таблица считает места через НАИБОЛЬШИЙ)", False),
    (GAP, "Ожидаемо: пробел в формулах таблицы", False),
    (MISMATCH, "Расхождения", True),
]
LEFTOVER_NOTES = {
    SeasonKind.TOUR: "турнир: закупки и выплаты должны совпадать",
    SeasonKind.CASH: "кэш: выплачено больше, чем закуплено",
}
SHEET_TAGS = {SeasonKind.TOUR: "Т", SeasonKind.CASH: "К"}
DATE_FIX_REASONS = {"wrong year": "неверный год", "text date": "дата текстом"}

GAMES = ("игра", "игры", "игр")
RESULTS = ("результат", "результата", "результатов")
DATES = ("дата", "даты", "дат")
MISMATCHES = ("расхождение", "расхождения", "расхождений")
LEFTOVERS = ("остаток не сходится", "остатка не сходятся", "остатков не сходятся")

FETCHED = "Таблица загружена. Проверьте отчёт и нажмите «Применить»."
RECOUNTED = "Отчёт пересчитан для выбранных листов."
NO_SHEETS = "Выберите хотя бы один лист."
GONE = "Этот снимок таблицы уже применён или устарел. Подтяните таблицу заново."
EXPIRED = "Снимок таблицы старше часа. Подтяните таблицу заново."
CHANGED = (
    "Данные в базе изменились после просмотра: ничего не применено. "
    "Проверьте обновлённый отчёт и примените ещё раз."
)
NOT_APPLICABLE = "Этот отчёт нельзя применить: исправьте ошибки в нём."
NOTHING_TO_APPLY = "Нет изменений: применять нечего."
DATE_NOT_FOUND = "Дата {} не найдена в выбранных листах."
DATES_GONE = "Выбранных дат нет в листах снимка. Подтяните таблицу заново."
ALL_DATES = "Все даты выбранных листов."
PARTIAL = "Частичный импорт: только {}."
PARTIAL_REST = "Остальные даты не тронуты."
PARTIAL_VERIFICATION = (
    "Сверка с итогами таблицы: импорт частичный, итоги таблицы считают все даты, поэтому "
    "расхождения здесь не показаны."
)
WARNINGS = "Предупреждения: "
WARNINGS_TAIL = ". Импорт их не исправляет, их правят в таблице."
DELETIONS = "Будут удалены результаты, которых нет в таблице: "
REFUSALS = "Не импортируются (сезон не ведётся в таблице): "
APPLY_NOTE = "Запишет ровно этот отчёт, из того же снимка."
NO_CHANGES = "Нет изменений."
HAS_ERRORS = "Есть ошибки: применять нельзя."


class SheetSyncView(UnfoldModelAdminViewMixin, TemplateView):
    title = "Синхронизация с таблицей"
    permission_required = ()
    template_name = "importer/sync.html"

    def has_permission(self):
        return self.request.user.is_active and self.request.user.is_superuser

    def get(self, request, *args, **kwargs):
        SheetSnapshot.purge_expired()
        context = {"configured": sources.is_configured()}
        if context["configured"]:
            snapshot = self._snapshot(request.GET.get("snapshot"))
            if snapshot is not None:
                context |= preview(snapshot)
        return self.render_to_response(self.get_context_data(**context))

    def post(self, request, *args, **kwargs):
        SheetSnapshot.purge_expired()
        if not sources.is_configured():
            return redirect(self.page_url())
        action = request.POST.get("action")
        if action == "fetch":
            return self.fetch()
        if action == "recount":
            return self.recount()
        if action == "apply":
            return self.apply()
        return redirect(self.page_url())

    def page_url(self, snapshot: SheetSnapshot | None = None) -> str:
        url = reverse(f"admin:{SYNC_URL_NAME}")
        return f"{url}?snapshot={snapshot.pk}" if snapshot is not None else url

    def _snapshot(self, pk) -> SheetSnapshot | None:
        if not str(pk or "").isdigit():
            return None
        snapshot = SheetSnapshot.objects.filter(pk=pk).first()
        return None if snapshot is None or snapshot.expired else snapshot

    def fetch(self):
        SheetSnapshot.objects.all().delete()  # one preview at a time
        try:
            content = sources.fetch_sheet()
            titles = import_sheet_titles(content)
        except sources.SheetFetchError as exc:
            messages.error(self.request, str(exc))
            return redirect(self.page_url())
        except SheetError:
            messages.error(self.request, "Google вернул файл, который не читается как таблица.")
            return redirect(self.page_url())
        snapshot = SheetSnapshot.objects.create(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            fetched_by=self.request.user,
            sheets=titles,
        )
        messages.success(self.request, FETCHED)
        return redirect(self.page_url(snapshot))

    def recount(self):
        snapshot = self._snapshot(self.request.POST.get("snapshot"))
        if snapshot is None:
            messages.error(self.request, GONE)
            return redirect(self.page_url())
        titles = import_sheet_titles(bytes(snapshot.content))
        chosen = [title for title in titles if title in self.request.POST.getlist("sheets")]
        if not chosen:
            messages.error(self.request, NO_SHEETS)
            return redirect(self.page_url(snapshot))
        dates = posted_dates(self.request.POST.getlist("dates"))
        try:
            found = found_dates(parse_workbook(bytes(snapshot.content), chosen))
        except SheetError:
            found = None  # the preview shows the errors
        missing = [date for date in dates if found is not None and date not in found]
        if missing:
            messages.error(self.request, DATE_NOT_FOUND.format(f"{missing[0]:%d.%m.%Y}"))
            return redirect(self.page_url(snapshot))
        snapshot.sheets = chosen
        snapshot.dates = [date.isoformat() for date in dates]
        snapshot.save(update_fields=["sheets", "dates"])
        messages.success(self.request, RECOUNTED)
        return redirect(self.page_url(snapshot))

    def apply(self):
        pk, digest = self.request.POST.get("snapshot"), self.request.POST.get("digest", "")
        with transaction.atomic():
            snapshot = (
                SheetSnapshot.objects.select_for_update().filter(pk=pk).first()
                if str(pk or "").isdigit()
                else None
            )
            if snapshot is None:
                messages.error(self.request, GONE)
                return redirect(self.page_url())
            if snapshot.expired:
                snapshot.delete()
                messages.error(self.request, EXPIRED)
                return redirect(self.page_url())
            try:
                report = run_import(
                    bytes(snapshot.content),
                    sheets=snapshot.sheets,
                    dates=snapshot_dates(snapshot),
                    expected_digest=digest,
                )
            except ReportChanged:
                messages.error(self.request, CHANGED)
                return redirect(self.page_url(snapshot))
            except UnknownDates:
                messages.error(self.request, NOT_APPLICABLE)
                return redirect(self.page_url(snapshot))
            if not report.ok or not report.imported:
                messages.error(self.request, NOT_APPLICABLE)
                return redirect(self.page_url(snapshot))
            if not report.has_changes:  # nothing was written: no log entry, the snapshot stays
                messages.error(self.request, NOTHING_TO_APPLY)
                return redirect(self.page_url(snapshot))
            LogEntry.objects.log_actions(
                user_id=self.request.user.pk,
                queryset=[snapshot],
                action_flag=CHANGE,
                change_message=log_summary(report),
                single_object=True,
            )
            snapshot.delete()
        messages.success(self.request, "Применено. " + log_summary(report))
        context = {"configured": True, "applied": True, **present(report)}
        return self.render_to_response(self.get_context_data(**context))


def posted_dates(values: list[str]) -> list[datetime.date]:
    """The ticked dates of the form, sorted; values that are not ISO dates are dropped."""
    dates = set()
    for value in values:
        try:
            dates.add(datetime.date.fromisoformat(value))
        except ValueError:
            continue
    return sorted(dates)


def snapshot_dates(snapshot: SheetSnapshot) -> list[datetime.date] | None:
    return posted_dates(snapshot.dates) or None


def preview(snapshot: SheetSnapshot) -> dict:
    """Context of a dry run of the snapshot's chosen sheets and dates, with the digest
    "Применить" sends."""
    content = bytes(snapshot.content)
    try:
        titles = import_sheet_titles(content)
        report = run_import(
            content, sheets=snapshot.sheets, dates=snapshot_dates(snapshot), dry_run=True
        )
    except SheetError as exc:
        return {"snapshot": snapshot, "sheet_errors": exc.errors}
    except UnknownDates:
        return {"snapshot": snapshot, "sheet_errors": [DATES_GONE]}
    return {
        "snapshot": snapshot,
        "titles": [(title, title in snapshot.sheets) for title in titles],
        "date_months": date_months(report),
        "digest": report.digest(),
        "can_apply": report.ok and bool(report.imported) and report.has_changes,
        **present(report),
    }


@dataclass
class DateChoice:
    date: datetime.date
    short: str  # "04.10", the label
    spoken: str  # "04.10.2026", with the full sheet titles when there are tags: the a11y name
    checked: bool
    tags: list[str] = field(default_factory=list)  # "Т", "К": one per sheet, several sheets only


def date_months(report: ImportReport) -> list[tuple[str, list[DateChoice]]]:
    """The date checkboxes by month, newest first: ("Октябрь 2026", the month's dates)."""
    selected = set(report.dates or ())
    kinds = {sheet.title: sheet.kind for sheet in report.sheets}
    months: dict[tuple[int, int], list[DateChoice]] = {}
    for date, found in reversed(report.found_dates.items()):
        several = len(found) > 1
        months.setdefault((date.year, date.month), []).append(
            DateChoice(
                date,
                f"{date:%d.%m}",
                ", ".join([f"{date:%d.%m.%Y}", *found]) if several else f"{date:%d.%m.%Y}",
                date in selected,
                [SHEET_TAGS.get(kinds.get(title), "") for title in found] if several else [],
            )
        )
    return [(f"{MONTHS[month - 1]} {year}", dates) for (year, month), dates in months.items()]


def present(report: ImportReport) -> dict:
    """The report in Russian words, ready for the template (it formats nothing itself)."""
    totals = report.totals()
    changed_games = sum(len(changed_game_dates(sheet)) for sheet in report.sheets)
    places = sum(len(sheet.place_changes) for sheet in report.sheets)
    sections, quiet = [], []
    if report.ok:
        for sheet in report.sheets:
            if sheet.refused or has_changes(sheet) or sheet_warnings(report, sheet):
                sections.append(present_sheet(report, sheet, len(sections) + 1))
            else:
                quiet.append(quiet_row(report, sheet))
    return {
        "report": report,
        "scope_label": (
            PARTIAL.format(", ".join(f"{date:%d.%m.%Y}" for date in report.dates))
            if report.partial
            else ALL_DATES
        ),
        "scope_rest": PARTIAL_REST if report.partial else "",
        "strip": [
            ("игр создано", totals["games created"]),
            ("игр изменено", changed_games),
            ("результатов создано", totals["results created"]),
            ("результатов изменено", totals["results updated"]),
            ("мест изменено", places),
        ],
        "unchanged_line": unchanged_line(report, totals, changed_games),
        "alerts": alerts(report, sections),
        "apply_note": (
            HAS_ERRORS if not report.ok else APPLY_NOTE if report.has_changes else NO_CHANGES
        ),
        "bottom_line": results_line(totals) or NO_CHANGES,
        "sections": sections,
        "quiet_sheets": quiet,
    }


def changed_game_dates(sheet: SheetReport) -> set[datetime.date]:
    """Games that exist and get a result created, updated, deleted or a new place."""
    dates = {change.date for change in sheet.changes}
    dates |= {change.date for change in sheet.place_changes}
    return dates - set(sheet.created_games)


def has_changes(sheet: SheetReport) -> bool:
    return bool(
        sheet.changes
        or sheet.place_changes
        or sheet.created_games
        or sheet.counts["seasons created"]
    )


def sheet_leftovers(report: ImportReport, sheet: SheetReport) -> list[Leftover]:
    """Games whose leftover does not add up; on a partial import only the selected dates."""
    if sheet.verification is None:
        return []
    games = sheet.verification.leftover_games
    if report.partial:
        return [game for game in games if game.date in report.dates]
    return games


def sheet_mismatches(report: ImportReport, sheet: SheetReport) -> int:
    """Mismatches against the sheet's totals: none on a partial import (the totals count every
    date, so they would not be real)."""
    if sheet.verification is None or report.partial:
        return 0
    return sheet.verification.count(MISMATCH)


def sheet_warnings(report: ImportReport, sheet: SheetReport) -> bool:
    return bool(sheet_mismatches(report, sheet) or sheet_leftovers(report, sheet))


def counted(value: int, forms: tuple[str, str, str]) -> str:
    return f"{format_money(value)} {ru_plural(value, forms)}"


def unchanged_line(report: ImportReport, totals, changed_games: int) -> str:
    """ "Без изменений: 137 игр, 997 результатов. С живого экрана не тронуты: 18.10, 10.10." """
    games = totals["games unchanged"] - changed_games
    results = totals["results unchanged"]
    line = ""
    if games or results:
        line = f"Без изменений: {counted(games, GAMES)}, {counted(results, RESULTS)}."
    skipped = sorted(
        (date for sheet in report.sheets for date in sheet.skipped_games), reverse=True
    )
    if skipped:
        dates = ", ".join(f"{date:%d.%m}" for date in skipped)
        line = f"{line} С живого экрана не тронуты: {dates}.".strip()
    return line


@dataclass
class Alert:
    """A summary line with a red square: lead text, links to the sections, closing text."""

    lead: str
    links: list[tuple[str, str]]  # (text, anchor)
    tail: str


def alerts(report: ImportReport, sections: list[dict]) -> list[Alert]:
    found = []
    mismatches = sum(section["mismatches"] for section in sections)
    leftovers = sum(len(section["leftovers"]) for section in sections)
    links = []
    if mismatches:
        anchor = next(s["anchor"] for s in sections if s["mismatches"])
        links.append((counted(mismatches, MISMATCHES), f"{anchor}-verification"))
    if leftovers:
        anchor = next(s["anchor"] for s in sections if s["leftovers"])
        links.append((counted(leftovers, LEFTOVERS), f"{anchor}-leftover"))
    if links:
        found.append(Alert(WARNINGS, links, WARNINGS_TAIL))
    deleted = report.totals()["results deleted"]
    if deleted:
        anchor = next(s["anchor"] for s in sections if s["deleted"])
        found.append(Alert(DELETIONS, [(format_money(deleted), f"{anchor}-changes")], "."))
    refused = [(s["sheet"].title, s["anchor"]) for s in sections if s["sheet"].refused]
    if refused:
        found.append(Alert(REFUSALS, refused, "."))
    return found


def results_line(counts) -> str:
    """ "Создано 1, изменено 2 результата." (zero parts left out), or "" when nothing changes."""
    parts = [
        (verb, counts[key])
        for verb, key in (
            ("создано", "results created"),
            ("изменено", "results updated"),
            ("удалено", "results deleted"),
        )
        if counts[key]
    ]
    if not parts:
        return ""
    text = ", ".join(f"{verb} {format_money(value)}" for verb, value in parts)
    return f"{text[0].upper()}{text[1:]} {ru_plural(parts[-1][1], RESULTS)}."


@dataclass
class ChangeRow:
    date: datetime.date
    name: str
    nickname: str
    what: str
    old: str | None  # None: an empty value (faint dot)
    new: str | None


def change_rows(sheet: SheetReport) -> list[ChangeRow]:
    """Every change of the sheet in one table: places, buy-ins and payouts, new and deleted
    results; by date, then player."""
    rows = [
        ChangeRow(c.date, c.name, c.nickname, PLACE_LABEL, _text(c.old), _text(c.new))
        for c in sheet.place_changes
    ]
    for change in sheet.changes:
        if change.action == CREATED:
            rows.append(
                ChangeRow(change.date, change.name, change.nickname, CREATED_LABEL, None, None)
            )
        elif change.action == DELETED:
            rows.append(
                ChangeRow(change.date, change.name, change.nickname, DELETED_LABEL, None, None)
            )
        else:
            rows += [
                ChangeRow(
                    change.date,
                    change.name,
                    change.nickname,
                    FIELD_LABELS[name],
                    format_money(old),
                    format_money(new),
                )
                for name, (old, new) in change.diff.items()
            ]
    return sorted(
        rows,
        key=lambda r: (
            r.date,
            r.name.casefold(),
            r.nickname.casefold(),
            CHANGE_ORDER.index(r.what),
        ),
    )


def _text(value: int | None) -> str | None:
    return None if value is None else format_money(value)


@dataclass
class FindingRow:
    name: str
    nickname: str
    header: str
    ours: str
    sheet: str
    difference: str  # ours minus the sheet, signed


def verification_groups(sheet: SheetReport) -> list[tuple[str, bool, list[FindingRow]]]:
    verification = sheet.verification
    groups = []
    for label, title, alert in FINDING_GROUPS:
        rows = [
            FindingRow(
                f.name,
                f.nickname,
                f.header,
                format_money(f.ours),
                format_money(f.sheet),
                format_net(f.ours - f.sheet),
            )
            for f in verification.findings
            if f.label == label
        ]
        if rows:
            groups.append((title, alert, rows))
    return groups


def present_sheet(report: ImportReport, sheet: SheetReport, number: int) -> dict:
    counts = sheet.counts
    changed = len(changed_game_dates(sheet))
    head = [
        f"{verb} {format_money(counts[key])}"
        for verb, key in (
            ("создано", "results created"),
            ("изменено", "results updated"),
            ("удалено", "results deleted"),
        )
        if counts[key]
    ]
    games, results = counts["games unchanged"] - changed, counts["results unchanged"]
    if games or results:
        head.append(f"без изменений {counted(games, GAMES)}, {counted(results, RESULTS)}")
    verification = sheet.verification
    return {
        "sheet": sheet,
        "anchor": f"sync-{number}",
        "head_line": " · ".join(head),
        "changes": change_rows(sheet),
        "places_note": PLACES_NOTE if sheet.place_changes else "",
        "deleted": counts["results deleted"],
        "verification": verification if verification and not report.partial else None,
        "verification_line": (
            f"проверено {format_money(verification.checked)}, "
            f"совпало {format_money(verification.ok)}"
            if verification
            else ""
        ),
        "partial_verification": PARTIAL_VERIFICATION if verification and report.partial else "",
        "findings": verification_groups(sheet) if verification and not report.partial else [],
        "mismatches": sheet_mismatches(report, sheet),
        "leftovers": [
            (game, LEFTOVER_NOTES.get(game.kind, ""), format_net(game.leftover))
            for game in sheet_leftovers(report, sheet)
        ],
        "date_fixes": [
            (fix, DATE_FIX_REASONS.get(fix.reason, fix.reason)) for fix in sheet.date_fixes
        ],
        "skipped": [
            (date, SKIPPED_LABELS[reason]) for date, reason in sorted(sheet.skipped_games.items())
        ],
        "not_selected": counted(len(sheet.not_selected), DATES) if sheet.not_selected else "",
        "not_selected_dates": ", ".join(f"{date:%d.%m.%Y}" for date in sheet.not_selected),
        "db_only": ", ".join(f"{date:%d.%m.%Y}" for date in sheet.db_only_games),
    }


def quiet_row(report: ImportReport, sheet: SheetReport) -> dict:
    """A sheet without changes or warnings: one row of the "Листы без изменений" table."""
    verification = sheet.verification
    return {
        "title": sheet.title,
        "note": "даты не выбраны" if sheet.nothing_selected else "",
        "games": None if sheet.nothing_selected else sheet.counts["games unchanged"],
        "results": None if sheet.nothing_selected else sheet.counts["results unchanged"],
        "verification": (
            f"{format_money(verification.ok)} из {format_money(verification.checked)}"
            if verification and not report.partial
            else ""
        ),
    }


def log_summary(report: ImportReport) -> str:
    """One line for the admin log: which sheets, what changed, what was left alone."""
    totals = report.totals()
    places = sum(len(sheet.place_changes) for sheet in report.sheets)
    parts = [
        "Импорт из таблицы: " + ", ".join(sheet.title for sheet in report.imported),
        f"игр создано {totals['games created']}",
        f"результатов создано {totals['results created']}, изменено "
        f"{totals['results updated']}, удалено {totals['results deleted']}",
        f"мест изменено {places}",
    ]
    if report.refused:
        parts.append("отклонены: " + ", ".join(sheet.title for sheet in report.refused))
    skipped = [
        f"{date:%d.%m.%Y}" for sheet in report.sheets for date in sorted(sheet.skipped_games)
    ]
    if skipped:
        parts.append("живые игры не тронуты: " + ", ".join(skipped))
    if report.partial:
        parts.append("только даты: " + ", ".join(f"{date:%d.%m.%Y}" for date in report.dates))
    return "; ".join(parts) + "."


@admin.register(SheetSnapshot)
class SheetSnapshotAdmin(ModelAdmin):
    """No list or forms: the model only backs the sync page, which its changelist redirects to."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        view = self.admin_site.admin_view(SheetSyncView.as_view(model_admin=self))
        return [path("sync/", view, name=SYNC_URL_NAME), *super().get_urls()]

    def changelist_view(self, request, extra_context=None):
        return redirect(f"admin:{SYNC_URL_NAME}")
