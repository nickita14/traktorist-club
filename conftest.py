"""Fixtures shared by the apps' test suites."""

import itertools
import os
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders

# Every Content-Security-Policy violation on the page, as "directive blocked-uri". Add it with
# context.add_init_script() and read window.cspViolations.
COLLECT_CSP_VIOLATIONS = """
window.cspViolations = [];
document.addEventListener('securitypolicyviolation', event => {
  window.cspViolations.push(`${event.violatedDirective} ${event.blockedURI}`);
});
"""

# Traces and screenshots of failed browser tests, one folder per test (CI uploads it).
BROWSER_ARTIFACTS = Path(__file__).resolve().parent / "test-results" / "playwright"
FAILED = pytest.StashKey[bool]()


def pytest_collection_modifyitems(items):
    """Mark every test that needs Chromium (through the ``browser`` fixture) as ``browser``.

    CI runs ``-m "not browser"`` and ``-m browser`` in separate jobs: the fixture, not the file
    name, decides which one a test belongs to.
    """
    for item in items:
        if "browser" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.browser)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed:
        item.stash[FAILED] = True
    return report


@pytest.fixture(autouse=True)
def trace_failed_browser_test(request):
    """Record a Playwright trace of every context a browser test opens; keep it on failure.

    When the setup or the test itself failed, closing a context saves a screenshot of each page
    and the trace to BROWSER_ARTIFACTS (open it with `uv run playwright show-trace <zip>`).
    """
    if "browser" not in request.fixturenames:
        yield
        return
    browser = request.getfixturevalue("browser")
    target = BROWSER_ARTIFACTS / re.sub(r"[^\w.-]+", "_", request.node.nodeid)
    numbers = itertools.count(1)
    new_context = browser.new_context

    def traced_context(**kwargs):
        context = new_context(**kwargs)
        context.tracing.start(screenshots=True, snapshots=True)
        close = context.close

        def close_and_keep_failures(**close_kwargs):
            if request.node.stash.get(FAILED, False):
                name = f"context-{next(numbers)}"
                target.mkdir(parents=True, exist_ok=True)
                for index, page in enumerate(context.pages, 1):
                    page.screenshot(path=target / f"{name}-page-{index}.png", full_page=True)
                context.tracing.stop(path=target / f"{name}-trace.zip")
            else:
                context.tracing.stop()
            close(**close_kwargs)

        context.close = close_and_keep_failures
        return context

    browser.new_context = traced_context
    yield
    browser.new_context = new_context


@pytest.fixture(scope="module")
def browser():
    """Headless Chromium through Playwright (tests marked ``browser``).

    Needs `uv run playwright install chromium` (on Linux also its system libraries:
    `uv run playwright install-deps chromium`). Without it the tests skip, unless
    REQUIRE_BROWSER_TESTS is set, as in CI.
    """
    sync_api = pytest.importorskip("playwright.sync_api")
    # Playwright's sync API keeps an event loop running in this thread, which makes Django refuse
    # ORM calls from the test body; the live server runs its requests in its own thread anyway.
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    playwright = sync_api.sync_playwright().start()
    try:
        chromium = playwright.chromium.launch()
    except sync_api.Error as error:
        playwright.stop()
        if os.environ.get("REQUIRE_BROWSER_TESTS"):
            raise
        pytest.skip(f"Chromium is not available: {error}")
    if not finders.find(settings.TAILWIND_CLI_DIST_CSS):
        chromium.close()
        playwright.stop()
        pytest.fail(
            f"{settings.TAILWIND_CLI_DIST_CSS} missing, run tailwind build "
            "(uv run python manage.py tailwind build): browser tests would measure unstyled pages.",
            pytrace=False,
        )
    yield chromium
    chromium.close()
    playwright.stop()
    del os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"]
