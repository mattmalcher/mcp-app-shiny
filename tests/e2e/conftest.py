"""Browser e2e fixtures: sandbox + Shiny app processes and Playwright pages.

Traces and screenshots of failing tests are written to test-results/.
Set E2E_BROWSER=firefox to run against Firefox (Chromium is the default).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from tests import procs

ROOT = procs.ROOT
RESULTS = ROOT / "test-results"
ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture(scope="session")
def sandbox_server():
    p = procs.sandbox_server(procs.free_port())
    yield p
    p.stop()


@pytest.fixture(scope="session")
def sandbox_url(sandbox_server) -> str:
    # "localhost" vs the app's "127.0.0.1": distinct origin and cookie jar.
    return f"http://localhost:{sandbox_server.port}/sandbox.html"


def _app(path: Path, mcp_url: str, sandbox_server) -> Iterator[procs.Proc]:
    env = {
        "MCP_SERVER_URL": mcp_url,
        "SHINY_MCP_SANDBOX_URL": f"http://localhost:{sandbox_server.port}/sandbox.html",
        # Examples start an in-process sandbox; point them at the harness one.
        "SANDBOX_PORT": str(sandbox_server.port),
    }
    p = procs.shiny_app(path, procs.free_port(), env)
    yield p
    p.stop()


@pytest.fixture(scope="session")
def fixture_app(fixture_server, sandbox_server):
    yield from _app(ROOT / "tests/e2e/apps/fixture_app.py", f"{fixture_server.url}/mcp", sandbox_server)


@pytest.fixture(scope="session")
def standalone_app(demo_server, sandbox_server):
    yield from _app(ROOT / "examples/standalone/app.py", f"{demo_server.url}/mcp", sandbox_server)


@pytest.fixture(scope="session")
def chat_app(demo_server, sandbox_server):
    yield from _app(ROOT / "examples/chat/app.py", f"{demo_server.url}/mcp", sandbox_server)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    name = os.environ.get("E2E_BROWSER", "chromium")
    with sync_playwright() as p:
        b = getattr(p, name).launch()
        yield b
        b.close()


class PageErrors(list):
    def assert_none(self) -> None:
        assert not self, f"uncaught page errors: {list(self)}"


def _new_context(browser: Browser, request, suffix: str = "") -> tuple[BrowserContext, Page, PageErrors]:
    context = browser.new_context(viewport={"width": 1200, "height": 1000})
    context.tracing.start(screenshots=True, snapshots=True)
    page = context.new_page()
    errors = PageErrors()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(15_000)
    return context, page, errors


def _finish(context: BrowserContext, page: Page, request, suffix: str = "") -> None:
    failed = getattr(request.node, "rep_call", None) is not None and request.node.rep_call.failed
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", request.node.nodeid) + suffix
    if failed:
        RESULTS.mkdir(exist_ok=True)
        try:
            page.screenshot(path=str(RESULTS / f"{name}.png"), full_page=True)
        except Exception:
            pass
        context.tracing.stop(path=str(RESULTS / f"{name}.trace.zip"))
    else:
        context.tracing.stop()
    context.close()


@pytest.fixture
def page_with_errors(browser, request) -> Iterator[tuple[Page, PageErrors]]:
    context, page, errors = _new_context(browser, request)
    yield page, errors
    _finish(context, page, request)


@pytest.fixture
def new_page(browser, request):
    """Factory for extra isolated browser contexts (e.g. a second user session)."""
    made = []

    def make() -> tuple[Page, PageErrors]:
        context, page, errors = _new_context(browser, request)
        made.append((context, page))
        return page, errors

    yield make
    for i, (context, page) in enumerate(made):
        _finish(context, page, request, f"-ctx{i}")
