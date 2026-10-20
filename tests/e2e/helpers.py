from __future__ import annotations

from typing import Any

from playwright.sync_api import FrameLocator, Locator, Page, expect


def widget(page: Page, widget_id: str) -> Locator:
    return page.locator(f"shiny-mcp-widget[data-widget-id='{widget_id}']")


def outer_frame(scope: Page | Locator, widget_id: str | None = None) -> FrameLocator:
    if widget_id is not None:
        scope = widget(scope, widget_id)  # type: ignore[arg-type]
    return scope.frame_locator("iframe.shiny-mcp-widget-frame")


def view(scope: Page | Locator, widget_id: str | None = None) -> FrameLocator:
    """The inner (untrusted) View document of a widget."""
    return outer_frame(scope, widget_id).frame_locator("iframe")


def wait_shiny(page: Page) -> None:
    page.wait_for_function("() => window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected()")


def cmd(page: Page, **command: Any) -> None:
    wait_shiny(page)
    page.evaluate("c => Shiny.setInputValue('cmd', c, {priority: 'event'})", command)


def wait_ready(page: Page, widget_id: str, timeout: float = 15_000) -> None:
    expect(widget(page, widget_id)).to_have_attribute("data-state", "ready", timeout=timeout)
