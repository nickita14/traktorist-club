"""Fixtures shared by the apps' test suites."""

import os

import pytest

# Every Content-Security-Policy violation on the page, as "directive blocked-uri". Add it with
# context.add_init_script() and read window.cspViolations.
COLLECT_CSP_VIOLATIONS = """
window.cspViolations = [];
document.addEventListener('securitypolicyviolation', event => {
  window.cspViolations.push(`${event.violatedDirective} ${event.blockedURI}`);
});
"""


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
    yield chromium
    chromium.close()
    playwright.stop()
    del os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"]
