"""A15 baseline: drive the upstream ext-apps basic-host against an MCP App server.

Usage: python scripts/basic_host_baseline.py http://localhost:8080

Asserts the same observable behaviour as tests/e2e/test_standalone.py:
initial tool result renders, and the View's "Get Server Time" button triggers
a second tools/call whose result updates the DOM.
"""

from __future__ import annotations

import re
import sys

from playwright.sync_api import expect, sync_playwright

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def main(url: str) -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.get_by_role("button", name="Call Tool").click()
        view = page.frame_locator("iframe").first.frame_locator("iframe").first
        time_el = view.locator("#server-time")
        expect(time_el).to_have_text(ISO, timeout=15000)
        first = time_el.inner_text()
        page.wait_for_timeout(5)
        view.get_by_role("button", name="Get Server Time").click()
        expect(time_el).not_to_have_text(first, timeout=10000)
        expect(time_el).to_have_text(ISO)
        second = time_el.inner_text()
        assert second > first, (first, second)
        browser.close()
        print(f"basic-host baseline OK: initial={first} after_click={second}")
        if errors:
            print("page errors:", errors)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"))
