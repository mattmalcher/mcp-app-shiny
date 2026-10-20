"""A04-A08 with the unmodified official ext-apps "Get Server Time" example server."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import ISO_TIME
from tests.e2e.helpers import view, wait_ready, widget

pytestmark = pytest.mark.e2e


def test_standalone_render_and_view_tool_call(standalone_app, page_with_errors):
    page, errors = page_with_errors
    page.goto(standalone_app.url)
    # A04: handshake completes (state "ready" is set after ui/notifications/initialized).
    wait_ready(page, "demo_widget")
    v = view(page, "demo_widget")
    time_el = v.locator("#server-time")
    # A05/A07: the initial tool result is displayed inside the Shiny page.
    expect(time_el).to_have_text(ISO_TIME)
    first = time_el.inner_text()
    # A06: a View-originated tools/call goes through Python and updates the DOM.
    page.wait_for_timeout(10)
    v.get_by_role("button", name="Get Server Time").click()
    expect(time_el).not_to_have_text(first)
    expect(time_el).to_have_text(ISO_TIME)
    assert time_el.inner_text() > first
    # ui/message reaches the Python callback and a Shiny output.
    v.get_by_role("button", name="Send Message").click()
    expect(page.locator("#messages")).to_contain_text("[demo_widget] user: This is message text.")
    # The sandbox is on a different origin than the app.
    frame_src = widget(page, "demo_widget").locator("iframe").get_attribute("src")
    assert frame_src.startswith("http://localhost:") and "127.0.0.1" in page.url
    errors.assert_none()


def test_standalone_rerender_replaces_view(standalone_app, page_with_errors):
    page, errors = page_with_errors
    page.goto(standalone_app.url)
    wait_ready(page, "demo_widget")
    first_render = widget(page, "demo_widget").get_attribute("data-render-id")
    page.get_by_role("button", name="Call get-time again").click()
    expect(widget(page, "demo_widget")).not_to_have_attribute("data-render-id", first_render)
    wait_ready(page, "demo_widget")
    expect(widget(page, "demo_widget").locator("iframe.shiny-mcp-widget-frame")).to_have_count(1)
    expect(view(page, "demo_widget").locator("#server-time")).to_have_text(ISO_TIME)
    errors.assert_none()


def _ask(page, text: str) -> None:
    box = page.get_by_role("textbox", name="Chat message")
    box.click()
    box.fill(text)
    page.keyboard.press("Enter")


def test_chat_widget_in_assistant_message(chat_app, page_with_errors):
    page, errors = page_with_errors
    page.goto(chat_app.url)
    _ask(page, "What time is it?")
    w1 = page.locator(".shiny-chat-message shiny-mcp-widget").first
    expect(w1).to_have_attribute("data-state", "ready")
    # A08: the View is nested in a generated assistant message, not the user one.
    assert w1.evaluate("e => !!e.closest('.shiny-chat-message') && !e.closest('.shiny-chat-user-message')")
    v1 = view(w1)
    expect(v1.locator("#server-time")).to_have_text(ISO_TIME)
    first = v1.locator("#server-time").inner_text()
    page.wait_for_timeout(10)
    v1.get_by_role("button", name="Get Server Time").click()
    expect(v1.locator("#server-time")).not_to_have_text(first)

    # A second chat response: the first View must remain interactive.
    _ask(page, "time again please")
    widgets = page.locator(".shiny-chat-message shiny-mcp-widget")
    expect(widgets).to_have_count(2)
    expect(widgets.nth(1)).to_have_attribute("data-state", "ready")
    expect(view(widgets.nth(1)).locator("#server-time")).to_have_text(ISO_TIME)
    before = v1.locator("#server-time").inner_text()
    page.wait_for_timeout(10)
    v1.get_by_role("button", name="Get Server Time").click()
    expect(v1.locator("#server-time")).not_to_have_text(before)
    expect(widgets.first).to_have_attribute("data-state", "ready")

    # The View's ui/message is posted back into the conversation.
    v1.get_by_role("button", name="Send Message").click()
    expect(page.locator(".shiny-chat-user-message").last).to_contain_text("This is message text.")
    errors.assert_none()
