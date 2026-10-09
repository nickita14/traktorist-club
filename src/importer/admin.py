"""The sheet sync page: pull the club Google Sheet, preview the import, apply what was previewed.

Superusers only: the section is hidden from everyone else and the page answers 403 to organizers
(anonymous visitors go to the admin login). The flow:

1. "Подтянуть из таблицы" fetches the workbook (importer.sources) into a SheetSnapshot, deleting
   any older one, and shows the dry-run report (importer.service) of the chosen sheets.
2. "Применить" imports the stored bytes, never a new fetch, and only while the import makes
   exactly the changes the page showed: the report's digest travels with the form, and a
   different result (the database changed meanwhile) is rolled back. The snapshot is deleted
   and the apply is logged (LogEntry).

Snapshots older than SheetSnapshot.TTL are refused and purged on every request to the page.
"""

import hashlib
from collections import Counter

from django.contrib import admin, messages
from django.contrib.admin.models import CHANGE, LogEntry
from django.db import transaction
from django.shortcuts import redirect
from django.urls import path, reverse
from django.views.generic import TemplateView
from unfold.admin import ModelAdmin
from unfold.views import UnfoldModelAdminViewMixin

from importer import sources
from importer.models import SheetSnapshot
from importer.report import (
    CREATED,
    DELETED,
    LIVE_IN_PROGRESS,
    LIVE_RECORDED,
    UPDATED,
    ImportReport,
    SheetReport,
)
from importer.service import ReportChanged, run_import
from importer.verify import GAP, MISMATCH, TIE
from importer.workbook import SheetError, import_sheet_titles

SYNC_URL_NAME = "importer_sheetsnapshot_sync"

COUNT_LABELS = {
    "seasons created": "сезонов создано",
    "games created": "игр создано",
    "games unchanged": "игр без изменений",
    "results created": "результатов создано",
    "results updated": "результатов изменено",
    "results unchanged": "результатов без изменений",
    "results deleted": "результатов удалено",
}
FIELD_LABELS = {"buyin": "закупка", "payout": "выплата"}
SKIPPED_LABELS = {
    LIVE_RECORDED: "записана на живом экране, не тронута",
    LIVE_IN_PROGRESS: "идёт живая игра, не тронута",
}
FINDING_LABELS = {
    TIE: "ожидаемо: ничья (таблица считает места через НАИБОЛЬШИЙ)",
    GAP: "ожидаемо: пробел в формулах таблицы",
    MISMATCH: "расхождение",
}
DATE_FIX_REASONS = {"wrong year": "неверный год", "text date": "дата текстом"}

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
        snapshot.sheets = chosen
        snapshot.save(update_fields=["sheets"])
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
                    bytes(snapshot.content), sheets=snapshot.sheets, expected_digest=digest
                )
            except ReportChanged:
                messages.error(self.request, CHANGED)
                return redirect(self.page_url(snapshot))
            if not report.ok or not report.imported:
                messages.error(self.request, NOT_APPLICABLE)
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


def preview(snapshot: SheetSnapshot) -> dict:
    """Context of a dry run of the snapshot's chosen sheets, with the digest "Применить" sends."""
    content = bytes(snapshot.content)
    try:
        titles = import_sheet_titles(content)
        report = run_import(content, sheets=snapshot.sheets, dry_run=True)
    except SheetError as exc:
        return {"snapshot": snapshot, "sheet_errors": exc.errors}
    return {
        "snapshot": snapshot,
        "titles": [(title, title in snapshot.sheets) for title in titles],
        "digest": report.digest(),
        "can_apply": report.ok and bool(report.imported),
        **present(report),
    }


def present(report: ImportReport) -> dict:
    """The report in Russian words, ready for the template (it formats nothing itself)."""
    return {
        "report": report,
        "sheets": [present_sheet(sheet) for sheet in report.sheets],
        "totals": count_lines(report.totals()),
    }


def present_sheet(sheet: SheetReport) -> dict:
    changed = [c for c in sheet.changes if c.action == UPDATED]
    verification = sheet.verification
    return {
        "sheet": sheet,
        "counts": count_lines(sheet.counts),
        "created": [c for c in sheet.changes if c.action == CREATED],
        "updated": [
            (c, [(FIELD_LABELS[name], old, new) for name, (old, new) in c.diff.items()])
            for c in changed
        ],
        "deleted": [c for c in sheet.changes if c.action == DELETED],
        "skipped": [(date, SKIPPED_LABELS[reason]) for date, reason in sheet.skipped_games.items()],
        "date_fixes": [
            (fix, DATE_FIX_REASONS.get(fix.reason, fix.reason)) for fix in sheet.date_fixes
        ],
        "findings": [
            (
                FINDING_LABELS[label],
                label == MISMATCH,
                [f for f in verification.findings if f.label == label],
            )
            for label in (MISMATCH, GAP, TIE)
            if verification and verification.count(label)
        ],
    }


def count_lines(counts: Counter) -> list[tuple[str, int]]:
    return [(label, counts[key]) for key, label in COUNT_LABELS.items() if counts.get(key)]


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
