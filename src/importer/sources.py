"""Where the club spreadsheet comes from: the Google Sheet, shared "anyone with the link can view".

``fetch_sheet()`` is the one place that knows: an authenticated Drive API call can replace it
later without touching the importer. Standard library only, no credentials.

The sheet ID is the access link itself and the repository is public: it never appears in a
message, a log line or an error report (the locals are marked sensitive, urllib's exceptions are
dropped from the chain).
"""

import http.client
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

EXPORT_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"
SHEET_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
# Google answers the export from docs.google.com and redirects the download to a
# *.googleusercontent.com host; a sheet that is not shared redirects to the sign-in page.
EXPORT_HOST = "docs.google.com"
DOWNLOAD_SUFFIX = ".googleusercontent.com"
SIGN_IN_HOST = "accounts.google.com"
MAX_BYTES = 10 * 1024 * 1024
DEADLINE = 15  # seconds for the whole fetch, redirects and reading included
SOCKET_TIMEOUT = 5  # per blocking socket operation: a fetch ends by DEADLINE + this at worst
CHUNK = 64 * 1024
XLSX_SIGNATURE = b"PK\x03\x04"  # an .xlsx is a zip archive

SHARE_HINT = "Проверьте доступ к таблице: «Все, у кого есть ссылка», просмотр."
NOT_CONFIGURED = "Синхронизация выключена: не задан GOOGLE_SHEET_ID."
BAD_ID = "GOOGLE_SHEET_ID задан с ошибкой: это не похоже на ID таблицы."
NOT_SHARED = f"Таблица не открыта по ссылке. {SHARE_HINT}"
NOT_XLSX = "Таблица недоступна по ссылке (Google вернул не xlsx)."
NOT_FOUND = "Таблица не найдена (Google ответил 404): проверьте GOOGLE_SHEET_ID."
HTTP_ERROR = "Google ответил ошибкой {code}. Попробуйте позже."
UNEXPECTED_REDIRECT = "Google перенаправил на неожиданный адрес, загрузка остановлена."
NETWORK = "Не удалось скачать таблицу: нет связи с Google или он не ответил вовремя."
TOO_LARGE = "Файл таблицы больше 10 МБ, загрузка остановлена."


class SheetFetchError(Exception):
    """The sheet could not be fetched; the message is for the organizer, in Russian."""


def is_configured() -> bool:
    return bool(settings.GOOGLE_SHEET_ID)


@sensitive_variables("sheet_id", "request")
def fetch_sheet() -> bytes:
    """The workbook's bytes, checked to be a zip archive; SheetFetchError otherwise."""
    sheet_id = settings.GOOGLE_SHEET_ID
    if not sheet_id:
        raise SheetFetchError(NOT_CONFIGURED)
    if not SHEET_ID_RE.match(sheet_id):
        raise SheetFetchError(BAD_ID)

    deadline = time.monotonic() + DEADLINE
    opener = urllib.request.build_opener(_GoogleRedirects(deadline))
    request = urllib.request.Request(
        EXPORT_URL.format(sheet_id=sheet_id), headers={"User-Agent": "traktorist-club"}
    )
    try:
        with opener.open(request, timeout=SOCKET_TIMEOUT) as response:
            body = _read(response, deadline)
    except SheetFetchError:
        raise
    except urllib.error.HTTPError as exc:
        exc.close()
        raise SheetFetchError(_http_message(exc.code)) from None
    except (OSError, http.client.HTTPException):  # URLError, timeouts, broken responses
        raise SheetFetchError(NETWORK) from None

    if not body.startswith(XLSX_SIGNATURE):
        html = body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html"))
        raise SheetFetchError(f"{NOT_XLSX} {SHARE_HINT}" if html else NOT_XLSX)
    return body


def _http_message(code: int) -> str:
    if code in (401, 403):
        return NOT_SHARED
    if code == 404:
        return NOT_FOUND
    if 300 <= code < 400:  # a redirect urllib itself refuses (another scheme, too many)
        return UNEXPECTED_REDIRECT
    return HTTP_ERROR.format(code=code)


def _allowed_host(host: str) -> bool:
    return host == EXPORT_HOST or host.endswith(DOWNLOAD_SUFFIX)


class _GoogleRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only over HTTPS to Google's export and download hosts, within the
    deadline. urllib reuses the first request's socket timeout for every redirect."""

    max_redirections = 5

    def __init__(self, deadline: float):
        self.deadline = deadline

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        host = (target.hostname or "").lower()
        problem = None
        if host == SIGN_IN_HOST:
            problem = NOT_SHARED
        elif target.scheme != "https" or not _allowed_host(host):
            problem = UNEXPECTED_REDIRECT
        elif time.monotonic() >= self.deadline:
            problem = NETWORK
        if problem is not None:
            fp.close()
            raise SheetFetchError(problem)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read(response, deadline: float) -> bytes:
    length = response.headers.get("Content-Length", "")
    if length.isdigit() and int(length) > MAX_BYTES:
        raise SheetFetchError(TOO_LARGE)
    chunks, size = [], 0
    while chunk := response.read(CHUNK):
        size += len(chunk)
        if size > MAX_BYTES:
            raise SheetFetchError(TOO_LARGE)
        if time.monotonic() >= deadline:
            raise SheetFetchError(NETWORK)
        chunks.append(chunk)
    return b"".join(chunks)
