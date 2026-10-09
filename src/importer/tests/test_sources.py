"""Fetching the sheet from Google, with the HTTP layer faked: no test touches the network.

``http.client.HTTPSConnection`` is replaced by a fake that answers each host with raw HTTP bytes
(parsed by the real ``http.client.HTTPResponse``), so urllib's own redirect handling runs.
"""

import http.client
import io
import itertools

import pytest

from importer import sources
from importer.sources import SheetFetchError, fetch_sheet

SHEET_ID = "fake-sheet-id-0123456789abcdefghij"
XLSX = b"PK\x03\x04" + b"\x00" * 200
DOWNLOAD = "https://doc-0s-1c-sheets.googleusercontent.com/export/abc?x=1"


def response(status=200, body=b"", headers=None, reason="OK"):
    lines = [f"HTTP/1.1 {status} {reason}"]
    lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
    if "Content-Length" not in (headers or {}):
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def redirect(location):
    return response(302, headers={"Location": location}, reason="Found")


class FakeSocket:
    def __init__(self, raw: bytes):
        self.raw = raw

    def makefile(self, mode):
        return io.BytesIO(self.raw)


class FakeConnection:
    """One host's answer: raw response bytes, or an exception to raise."""

    routes: dict = {}
    requests: list = []
    # Read by urllib's HTTPSHandler when the opener is built.
    debuglevel = 0
    _http_vsn = 11

    def __init__(self, host, timeout=None, **kwargs):
        self.host, self.timeout, self.sock = host, timeout, None

    def set_debuglevel(self, level):
        pass

    def request(self, method, url, body=None, headers=None, **kwargs):
        self.requests.append((self.host, url, self.timeout))

    def getresponse(self):
        answer = self.routes[self.host]
        if isinstance(answer, Exception):
            raise answer
        result = http.client.HTTPResponse(FakeSocket(answer))
        result.begin()
        return result

    def close(self):
        pass


@pytest.fixture
def google(monkeypatch, settings):
    settings.GOOGLE_SHEET_ID = SHEET_ID
    FakeConnection.routes, FakeConnection.requests = {}, []
    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    return FakeConnection.routes


def fetch_error() -> str:
    """The message of a failed fetch, after checking that nothing in it shows the sheet ID."""
    with pytest.raises(SheetFetchError) as exc:
        fetch_sheet()
    error = exc.value
    assert SHEET_ID not in str(error)
    # urllib's exceptions carry the URL: they never travel with the error into a report.
    assert error.__cause__ is None
    assert error.__context__ is None or error.__suppress_context__
    return str(error)


class TestSuccess:
    def test_direct_answer(self, google):
        google["docs.google.com"] = response(body=XLSX)
        assert fetch_sheet() == XLSX
        host, url, timeout = FakeConnection.requests[0]
        assert url == f"/spreadsheets/d/{SHEET_ID}/export?format=xlsx"
        assert timeout == sources.SOCKET_TIMEOUT

    def test_redirect_to_the_download_host(self, google):
        google["docs.google.com"] = redirect(DOWNLOAD)
        google["doc-0s-1c-sheets.googleusercontent.com"] = response(body=XLSX)

        assert fetch_sheet() == XLSX
        assert [host for host, _, _ in FakeConnection.requests] == [
            "docs.google.com",
            "doc-0s-1c-sheets.googleusercontent.com",
        ]

    def test_body_without_content_length(self, google):
        google["docs.google.com"] = response(body=XLSX, headers={"Connection": "close"})
        google["docs.google.com"] = google["docs.google.com"].replace(
            f"Content-Length: {len(XLSX)}\r\n".encode(), b""
        )
        assert fetch_sheet() == XLSX


class TestNotConfigured:
    def test_empty_id(self, settings):
        settings.GOOGLE_SHEET_ID = ""
        assert not sources.is_configured()
        with pytest.raises(SheetFetchError, match="не задан GOOGLE_SHEET_ID"):
            fetch_sheet()

    def test_malformed_id_is_never_put_into_a_url(self, settings, monkeypatch):
        settings.GOOGLE_SHEET_ID = "../../evil?x=" + "a" * 30
        monkeypatch.setattr(http.client, "HTTPSConnection", None)  # any request would fail
        with pytest.raises(SheetFetchError, match="задан с ошибкой"):
            fetch_sheet()


class TestNotShared:
    def test_html_instead_of_a_file(self, google):
        page = b"<!DOCTYPE html><html><body>Sign in</body></html>"
        google["docs.google.com"] = response(body=page, headers={"Content-Type": "text/html"})

        message = fetch_error()

        assert message.startswith("Таблица недоступна по ссылке (Google вернул не xlsx).")
        assert "Все, у кого есть ссылка" in message

    def test_other_body_is_not_xlsx(self, google):
        google["docs.google.com"] = response(body=b"just text")
        assert fetch_error() == "Таблица недоступна по ссылке (Google вернул не xlsx)."

    @pytest.mark.parametrize("status", [401, 403])
    def test_refused(self, google, status):
        google["docs.google.com"] = response(status, body=b"no", reason="Forbidden")
        assert fetch_error() == sources.NOT_SHARED

    def test_redirect_to_sign_in(self, google):
        google["docs.google.com"] = redirect("https://accounts.google.com/ServiceLogin?continue=x")
        assert fetch_error() == sources.NOT_SHARED
        assert len(FakeConnection.requests) == 1  # the sign-in page is never requested


class TestRedirects:
    @pytest.mark.parametrize(
        "location",
        [
            "https://evil.example.com/sheet.xlsx",
            "https://googleusercontent.com.evil.example/x",  # suffix, not a subdomain
            "https://docs.google.com.evil.example/x",
            "http://doc-0s-1c-sheets.googleusercontent.com/x",  # not HTTPS
            "file:///etc/passwd",
        ],
    )
    def test_other_targets_are_refused(self, google, location):
        google["docs.google.com"] = redirect(location)
        assert fetch_error() == sources.UNEXPECTED_REDIRECT
        assert len(FakeConnection.requests) == 1


class TestErrors:
    def test_not_found(self, google):
        google["docs.google.com"] = response(404, body=b"nope", reason="Not Found")
        assert fetch_error() == sources.NOT_FOUND

    def test_redirect_loop(self, google):
        google["docs.google.com"] = redirect("https://docs.google.com/again")
        assert fetch_error() == sources.UNEXPECTED_REDIRECT

    def test_server_error(self, google):
        google["docs.google.com"] = response(500, body=b"oops", reason="Server Error")
        assert fetch_error() == "Google ответил ошибкой 500. Попробуйте позже."

    def test_timeout(self, google):
        google["docs.google.com"] = TimeoutError("timed out")
        assert fetch_error() == sources.NETWORK

    def test_network_down(self, google):
        google["docs.google.com"] = ConnectionRefusedError("refused")
        assert fetch_error() == sources.NETWORK

    def test_broken_response(self, google):
        google["docs.google.com"] = b"garbage, not HTTP"
        assert fetch_error() == sources.NETWORK


class TestLimits:
    def test_declared_size_over_the_limit(self, google, monkeypatch):
        monkeypatch.setattr(sources, "MAX_BYTES", 100)
        google["docs.google.com"] = response(body=XLSX)  # 204 bytes, Content-Length says so
        assert fetch_error() == sources.TOO_LARGE

    def test_size_enforced_while_reading(self, google, monkeypatch):
        monkeypatch.setattr(sources, "MAX_BYTES", 100)
        monkeypatch.setattr(sources, "CHUNK", 64)
        raw = response(body=XLSX).replace(f"Content-Length: {len(XLSX)}\r\n".encode(), b"")
        google["docs.google.com"] = raw  # no Content-Length: read until the connection closes
        assert fetch_error() == sources.TOO_LARGE

    def test_overall_deadline_while_reading(self, google, monkeypatch):
        # Every clock reading is 4 s later: the body trickles in past the 15 s deadline.
        clock = itertools.count(0, 4)
        monkeypatch.setattr(sources.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(sources, "CHUNK", 16)
        google["docs.google.com"] = response(body=XLSX)
        assert fetch_error() == sources.NETWORK

    def test_overall_deadline_across_redirects(self, google, monkeypatch):
        readings = iter([0, 16])  # the start, then the redirect: past the deadline
        monkeypatch.setattr(sources.time, "monotonic", lambda: next(readings))
        google["docs.google.com"] = redirect(DOWNLOAD)
        google["doc-0s-1c-sheets.googleusercontent.com"] = response(body=XLSX)

        assert fetch_error() == sources.NETWORK
        assert len(FakeConnection.requests) == 1
