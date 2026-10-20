"""A05, A09-A12 in a real browser with the fixture MCP server."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from tests.e2e.helpers import cmd, view, wait_ready, widget

pytestmark = pytest.mark.e2e


@pytest.fixture
def app_page(fixture_app, page_with_errors):
    page, errors = page_with_errors
    page.goto(fixture_app.url)
    return page, errors


def render(page, target: str, tool: str = "counter", **args) -> None:
    cmd(page, action="render", tool=tool, target=target, args=args)


def test_a05_initial_input_and_result_delivered_exactly_once(app_page):
    page, errors = app_page
    render(page, "w_a", start=41)
    wait_ready(page, "w_a")
    v = view(page, "w_a")
    expect(v.locator("#count")).to_have_text("41")
    expect(v.locator("#tool-input")).to_have_text('{"start":41}')
    page.wait_for_timeout(500)
    expect(v.locator("#input-count")).to_have_text("1")
    expect(v.locator("#result-count")).to_have_text("1")
    errors.assert_none()


def test_a09_model_context_isolated_per_widget(app_page):
    page, errors = app_page
    render(page, "w_a", start=1)
    render(page, "w_b", start=100)
    wait_ready(page, "w_a")
    wait_ready(page, "w_b")
    a, b = view(page, "w_a"), view(page, "w_b")
    a.get_by_role("button", name="Increment").click()
    expect(a.locator("#count")).to_have_text("2")
    a.get_by_role("button", name="Share context").click()
    expect(page.locator("#contexts")).to_have_text(json.dumps({"w_a": {"count": 2}}))
    b.get_by_role("button", name="Share context").click()
    expect(page.locator("#contexts")).to_have_text(json.dumps({"w_a": {"count": 2}, "w_b": {"count": 100}}))
    b.get_by_role("button", name="Send message").click()
    expect(page.locator("#messages")).to_have_text("w_b:count is 100")
    errors.assert_none()


def test_a10_parallel_widgets_do_not_mix_responses(app_page):
    page, errors = app_page
    render(page, "w_a", start=0)
    render(page, "w_b", start=1000)
    wait_ready(page, "w_a")
    wait_ready(page, "w_b")
    a, b = view(page, "w_a"), view(page, "w_b")
    # Interleave clicks so both Views have requests in flight at once.
    for _ in range(5):
        a.get_by_role("button", name="Increment").click()
        b.get_by_role("button", name="Increment").click()
        page.wait_for_timeout(120)
    expect(a.locator("#count")).to_have_text("5")
    expect(b.locator("#count")).to_have_text("1005")
    errors.assert_none()


@pytest.mark.parametrize(
    ("tool", "needle"),
    [("broken-mime", "Unsupported MIME type"), ("missing-ui", "Unknown resource"), ("huge-ui", "exceeds")],
)
def test_a11_readable_error_for_bad_resources(app_page, tool, needle):
    page, errors = app_page
    render(page, "w_err", tool=tool)
    alert = widget(page, "w_err").get_by_role("alert")
    expect(alert).to_contain_text(needle)
    expect(widget(page, "w_err")).to_have_attribute("data-state", "error")
    expect(widget(page, "w_err").locator("iframe")).to_have_count(0)
    errors.assert_none()


def test_a11_view_tool_errors_are_reported_and_app_stays_responsive(app_page):
    page, errors = app_page
    render(page, "w_a", start=5)
    wait_ready(page, "w_a")
    v = view(page, "w_a")
    v.get_by_role("button", name="Call model-only tool").click()
    expect(v.locator("#status")).to_contain_text("rejected: ")
    expect(v.locator("#status")).to_contain_text("not visible to apps")
    v.get_by_role("button", name="Increment").click()
    expect(v.locator("#count")).to_have_text("6")
    errors.assert_none()


def test_a12_unmount_and_remount(app_page):
    page, errors = app_page
    render(page, "w_b", start=7)
    wait_ready(page, "w_b")
    cmd(page, action="remove", target="w_b")
    expect(widget(page, "w_b")).to_have_count(0)
    # Re-inserting a widget with the same id remounts and re-renders the View.
    cmd(page, action="insert", target="w_b")
    wait_ready(page, "w_b")
    expect(view(page, "w_b").locator("#count")).to_have_text("7")
    expect(view(page, "w_b").locator("#input-count")).to_have_text("1")
    errors.assert_none()


def test_a12_session_disconnect_tears_down_views(app_page):
    page, errors = app_page
    render(page, "w_a", start=1)
    wait_ready(page, "w_a")
    page.evaluate("() => Shiny.shinyapp.$socket.close()")
    expect(widget(page, "w_a")).to_have_attribute("data-state", "disconnected")
    expect(widget(page, "w_a").locator("iframe")).to_have_count(0)
    expect(widget(page, "w_a").get_by_role("alert")).to_contain_text("Disconnected")
    errors.assert_none()


def test_multiple_sessions_identical_widget_ids_isolated(fixture_app, new_page):
    (p1, e1), (p2, e2) = new_page(), new_page()
    p1.goto(fixture_app.url)
    p2.goto(fixture_app.url)
    render(p1, "w_a", start=10)
    render(p2, "w_a", start=20)
    wait_ready(p1, "w_a")
    wait_ready(p2, "w_a")
    view(p1, "w_a").get_by_role("button", name="Increment").click()
    view(p2, "w_a").get_by_role("button", name="Share context").click()
    expect(view(p1, "w_a").locator("#count")).to_have_text("11")
    expect(view(p2, "w_a").locator("#count")).to_have_text("20")
    expect(p2.locator("#contexts")).to_have_text(json.dumps({"w_a": {"count": 20}}))
    p1.wait_for_timeout(300)
    expect(p1.locator("#contexts")).to_have_text("{}")
    e1.assert_none()
    e2.assert_none()
