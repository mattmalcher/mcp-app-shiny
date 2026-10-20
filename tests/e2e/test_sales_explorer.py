"""The showcase example: agent-made tool call rendered in chat, then a full round trip."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from tests.e2e.helpers import view

pytestmark = pytest.mark.e2e


def test_sales_explorer_round_trip(sales_app, page_with_errors):
    page, errors = page_with_errors
    page.goto(sales_app.url)
    box = page.get_by_role("textbox", name="Chat message")
    box.click()
    box.fill("Explore Q3 sales in the West")
    page.keyboard.press("Enter")

    # The agent's existing result is rendered (render_result) inside its reply.
    w = page.locator(".shiny-chat-message shiny-mcp-widget").first
    expect(w).to_have_attribute("data-state", "ready", timeout=20_000)
    v = view(w)
    expect(v.get_by_label("Quarter")).to_have_value("Q3")
    q3_total = v.locator("#total").inner_text()

    # Pre-selected region arrives in Python as model context.
    expect(page.locator("#selection")).to_contain_text('"region": "West"')

    # A filter change makes a View -> Python -> MCP tool call.
    v.get_by_label("Product").select_option("Gadgets")
    expect(v.locator("#total")).not_to_have_text(q3_total)
    v.get_by_role("button", name="South:", exact=False).click()
    expect(page.locator("#selection")).to_contain_text('"region": "South"')
    sel = json.loads(page.locator("#selection").inner_text())["selection"]
    assert sel["product"] == "Gadgets" and sel["quarter"] == "Q3"

    # The View's question reaches Python, which answers from the selection.
    v.get_by_role("button", name="Ask about this region").click()
    expect(page.locator(".shiny-chat-user-message").last).to_contain_text("What is driving sales in South")
    answer = page.locator(".shiny-chat-message").last
    expect(answer).to_contain_text("South brought in")
    expect(answer).to_contain_text(f"${sel['revenue']:,}")
    errors.assert_none()
