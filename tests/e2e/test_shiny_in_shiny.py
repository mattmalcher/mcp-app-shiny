"""Spike: an MCP App View that is itself a Shiny app, its websocket carried by MCP tool calls."""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from tests.e2e.helpers import view

pytestmark = pytest.mark.e2e


def _say(page, text: str) -> None:
    box = page.get_by_role("textbox", name="Chat message")
    box.click()
    box.fill(text)
    page.keyboard.press("Enter")


def _context(page) -> dict:
    return json.loads(page.locator("#context").inner_text())


def test_shiny_in_shiny_round_trip(shiny_in_shiny_app, page_with_errors):
    page, errors = page_with_errors
    page.goto(shiny_in_shiny_app.url)

    # The tool's arguments become the inner app's initial controls.
    _say(page, "Plot iris")
    w = page.locator(".shiny-chat-message shiny-mcp-widget").first
    expect(w).to_have_attribute("data-state", "ready", timeout=20_000)
    v = view(w)
    expect(v.locator("select#x")).to_have_value("petal_length", timeout=15_000)
    expect(v.locator("#plot svg")).to_have_attribute("aria-label", "petal_width against petal_length")
    expect(page.locator("#context")).to_contain_text('"x": "petal_length"')

    # Moving a control in the inner Shiny app reaches the host as model context.
    v.locator("select#x").select_option("sepal_length")
    expect(page.locator("#context")).to_contain_text('"x": "sepal_length"')
    expect(v.locator("#plot svg")).to_have_attribute("aria-label", "petal_width against sepal_length")

    # The model moves the inner app's controls with a tool call (server push via the long-poll).
    _say(page, "Switch to cars, colour by cylinders")
    expect(v.locator("select#dataset")).to_have_value("cars")
    expect(v.locator("select#color")).to_have_value("cylinders")
    expect(page.locator("#context")).to_contain_text('"dataset": "cars"')
    _say(page, "Show mpg against weight")
    expect(v.locator("#plot svg")).to_have_attribute("aria-label", "mpg against weight")
    expect(page.locator("#context")).to_contain_text('"x": "weight"')
    assert _context(page)["controls"] == {"dataset": "cars", "x": "weight", "y": "mpg", "color": "cylinders"}

    # The inner app's button sends a chat message; the host answers from the shared context.
    v.get_by_role("button", name="Ask about this plot").click()
    expect(page.locator(".shiny-chat-user-message").last).to_contain_text("cars plot of mpg against weight")
    expect(page.locator(".shiny-chat-message").last).to_contain_text("strong negative relationship")
    errors.assert_none()
