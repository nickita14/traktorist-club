"""Fixtures shared by the apps' test suites."""

import itertools
import os
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.staticfiles import finders
from django.test.utils import override_settings

# Every Content-Security-Policy violation on the page, as "directive blocked-uri". Add it with
# context.add_init_script() and read window.cspViolations.
COLLECT_CSP_VIOLATIONS = """
window.cspViolations = [];
document.addEventListener('securitypolicyviolation', event => {
  window.cspViolations.push(`${event.violatedDirective} ${event.blockedURI}`);
});
"""

# window.polls counts the polls htmx starts on a [data-poll] element, also those live.js holds back
# while the organizer types; window.pollSwaps counts the [data-poll] elements a poll swapped in.
# Add it with context.add_init_script() and wait on them with page.wait_for_function().
COUNT_POLLS = """
window.polls = 0;
window.pollSwaps = 0;
document.addEventListener('htmx:beforeRequest', event => {
  if (event.detail.elt.hasAttribute('data-poll')) window.polls += 1;
});
document.addEventListener('htmx:load', event => {
  if (event.target.matches && event.target.matches('[data-poll]')) window.pollSwaps += 1;
});
"""

# window.pageSettled turns true once the page is revealed and its view transition (if any) has
# finished. Add it with context.add_init_script() and wait with settle_navigation().
RECORD_SETTLED = """
window.pageSettled = false;
window.addEventListener('pagereveal', event => {
  const settle = () => { window.pageSettled = true; };
  if (event.viewTransition) event.viewTransition.finished.then(settle, settle);
  else settle();
});
"""


def settle_navigation(page):
    """After a navigation: wait until the page is styled and its transition has finished.

    Playwright can query the new page's DOM before its stylesheets apply (an unstyled standings
    table is wider than a phone), and document.getAnimations() does not list a view transition's
    animations, so this waits for the load event, the fonts and RECORD_SETTLED's flag.
    """
    page.wait_for_load_state("load")
    page.evaluate("document.fonts.ready")
    page.wait_for_function("() => window.pageSettled === true")  # a function: CSP forbids eval


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


@pytest.fixture(autouse=True, scope="session")
def fast_password_hasher():
    """MD5 instead of the default PBKDF2 (about 0.3 s per hash): most of the unit run's time.

    Only for the tests; override_settings resets Django's cached hashers on the way in and out.
    """
    with override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]):
        yield


# The transactional test whose flush already emptied this process's test database, if any.
FLUSHED_BY = pytest.StashKey[str]()


def database_use(item) -> str | None:
    """The test's database use as pytest-django sorts it: transactional, rollback or None."""
    fixtures = getattr(item, "fixturenames", ())
    marker = item.get_closest_marker("django_db")
    if (
        "transactional_db" in fixtures
        or "live_server" in fixtures
        or (marker and (marker.kwargs.get("transaction") or marker.kwargs.get("reset_sequences")))
    ):
        return "transactional"
    if marker or "db" in fixtures:
        return "rollback"
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    """Fail a rollback test that would run after a transactional one in the same process.

    A transactional test flushes the database, and with it the rows the data migrations seed
    (the Organizer group, the achievement rules), which rollback tests rely on. pytest-django
    sorts every rollback test before the transactional ones, and xdist keeps that order in each
    worker only with ``--dist load`` (verified on pytest-xdist 3.8). This guard catches a change
    there, or another order (``--dist loadfile``/``loadscope``, ``--ff``, ``--nf``), before the
    seed's absence fails tests in confusing ways.
    """
    use = database_use(item)
    flushed_by = item.config.stash.get(FLUSHED_BY, None)
    if use == "rollback" and flushed_by:
        pytest.fail(
            f"{flushed_by} flushed the migration seed earlier in this process, so this test "
            "would run without it. Keep the default --dist load (see CLAUDE.md, Code conventions).",
            pytrace=False,
        )
    if use == "transactional" and not flushed_by:
        item.config.stash[FLUSHED_BY] = item.nodeid


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


@pytest.fixture(scope="session")
def browser():
    """Headless Chromium through Playwright (tests marked ``browser``).

    Needs `uv run playwright install chromium` (on Linux also its system libraries:
    `uv run playwright install-deps chromium`). Without it the tests skip, unless
    REQUIRE_BROWSER_TESTS is set, as in CI.

    One per process (each xdist worker): a launch takes about 1.7 s. From the first browser test
    on, the rest of that process runs with Playwright's event loop and DJANGO_ALLOW_ASYNC_UNSAFE.
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
