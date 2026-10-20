"""A14 security / negative tests in a real browser.

The "evil" fixture View (official App SDK) attacks the host from inside the
sandbox and records the outcome of each attempt in its own DOM.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import urllib.request

import pytest
from playwright.sync_api import expect

from shiny_mcp_host.client import ServerConfig, ServerConnection
from tests.e2e.helpers import cmd, outer_frame, view, wait_ready, wait_shiny, widget

pytestmark = [pytest.mark.e2e, pytest.mark.security]

ATTACKS = [
    "parent-document",
    "top-document",
    "top-shiny",
    "cookie",
    "local-storage",
    "fetch-external",
    "popup",
    "top-navigation",
    "forged-top-message",
    "forged-resource-ready",
    "forbidden-tool",
    "javascript-link",
    "file-link",
]
# These attempts "succeed" at posting but must have no effect (checked below).
FIRE_AND_FORGET = {"forged-top-message", "forged-resource-ready"}


def fixture_stats(fixture_server) -> dict[str, int]:
    async def run():
        conn = ServerConnection(ServerConfig("fx", url=f"{fixture_server.url}/mcp"), timeout=10)
        try:
            r = await conn.call_tool("stats", {})
            return r.structured_content.get("result", r.structured_content)
        finally:
            await conn.close()

    # Sync Playwright owns this thread's event loop; use a fresh one elsewhere.
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, run()).result(timeout=30)


def test_malicious_view_is_contained(fixture_app, fixture_server, page_with_errors):
    page, errors = page_with_errors
    page.context.add_cookies([{"name": "shiny_secret", "value": "s3cret", "url": fixture_app.url}])
    page.goto(fixture_app.url)
    before = fixture_stats(fixture_server)["secret-admin"]
    cmd(page, action="render", tool="evil", target="w_evil")
    wait_ready(page, "w_evil")
    v = view(page, "w_evil")
    expect(v.locator("#status")).to_have_text("attacks done", timeout=20_000)
    results = {
        li.get_attribute("data-attack"): (li.get_attribute("data-blocked"), li.inner_text())
        for li in v.locator("#attacks li").all()
    }
    assert set(results) == set(ATTACKS)
    for name in ATTACKS:
        if name not in FIRE_AND_FORGET:
            assert results[name][0] == "true", f"attack not blocked: {results[name][1]}"
    # The cookie of the Shiny origin was not readable from the View.
    assert "s3cret" not in json.dumps(results)
    # Top navigation failed and no popup opened.
    assert page.url.startswith(fixture_app.url)
    assert len(page.context.pages) == 1
    # Shiny inputs were not reachable.
    expect(page.locator("#pwned")).to_have_text("pwned=None")
    # Forged resource-ready did not replace the View; forged tools/call did nothing.
    expect(v.locator("h1")).to_have_text("Evil view")
    page.wait_for_timeout(500)
    assert fixture_stats(fixture_server)["secret-admin"] == before
    # The View is still the only thing in the sandbox, with the strict defaults.
    inner = outer_frame(page, "w_evil").locator("iframe")
    assert inner.get_attribute("sandbox") == "allow-scripts allow-forms"
    assert (
        widget(page, "w_evil").locator("iframe").get_attribute("sandbox")
        == "allow-scripts allow-same-origin allow-forms"
    )
    errors.assert_none()


def test_forbidden_tool_from_counter_view_has_zero_invocations(fixture_app, fixture_server, page_with_errors):
    page, errors = page_with_errors
    page.goto(fixture_app.url)
    before = fixture_stats(fixture_server)["secret-admin"]
    cmd(page, action="render", tool="counter", target="w_a", args={"start": 1})
    wait_ready(page, "w_a")
    v = view(page, "w_a")
    v.get_by_role("button", name="Call forbidden tool").click()
    expect(v.locator("#status")).to_contain_text("not authorised")
    v.get_by_role("button", name="Open javascript link").click()
    expect(v.locator("#status")).to_have_text("link rejected")
    assert fixture_stats(fixture_server)["secret-admin"] == before
    errors.assert_none()


def test_forged_messages_from_wrong_window_origin_or_nonce_are_ignored(
    fixture_app, fixture_server, page_with_errors
):
    page, errors = page_with_errors
    page.goto(fixture_app.url)
    before = fixture_stats(fixture_server)["secret-admin"]
    cmd(page, action="render", tool="counter", target="w_a", args={"start": 3})
    wait_ready(page, "w_a")
    forged = {
        "jsonrpc": "2.0",
        "id": 4242,
        "method": "tools/call",
        "params": {"name": "secret-admin", "arguments": {}},
    }
    # 1. From the host page itself (wrong source window).
    page.evaluate("m => window.postMessage(m, '*')", forged)
    # 2. From an attacker iframe on another origin, including a fake proxy-ready.
    page.evaluate(
        """m => {
            const f = document.createElement('iframe');
            f.srcdoc = `<script>
              parent.postMessage(${JSON.stringify(m)}, '*');
              parent.postMessage({jsonrpc: '2.0', method: 'ui/notifications/sandbox-proxy-ready',
                                  params: {nonce: 'guess'}}, '*');
            <\\/script>`;
            document.body.appendChild(f);
        }""",
        forged,
    )
    page.wait_for_timeout(800)
    assert fixture_stats(fixture_server)["secret-admin"] == before
    # The real View is unaffected and still works.
    v = view(page, "w_a")
    v.get_by_role("button", name="Increment").click()
    expect(v.locator("#count")).to_have_text("4")
    errors.assert_none()


def test_sandbox_refuses_unsafe_embedding(sandbox_server, fixture_app, page_with_errors):
    page, _ = page_with_errors
    base = f"http://localhost:{sandbox_server.port}/sandbox.html"
    nonce = "n" * 32
    # Top-level load: the proxy refuses to run outside an iframe.
    msgs = []
    page.on("console", lambda m: msgs.append(m.text))
    page.goto(f"{base}?host=http://127.0.0.1:1&nonce={nonce}")
    page.wait_for_timeout(300)
    assert any("must be embedded in an iframe" in m for m in msgs)
    # A host origin outside the allowlist gets a 403 from the server.
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{base}?host=https://evil.example&nonce={nonce}")
    assert e.value.code == 403
    # frame-ancestors: only the app origin may frame a sandbox issued for it.
    assert (
        urllib.request.urlopen(f"{base}?host={fixture_app.url}&nonce={nonce}")
        .headers["Content-Security-Policy"]
        .endswith(f"frame-ancestors {fixture_app.url}")
    )


def test_host_fails_closed_on_same_origin_sandbox(fixture_app, page_with_errors):
    """If the configured sandbox were same-origin, the browser adapter refuses to render."""
    page, errors = page_with_errors
    page.goto(fixture_app.url)
    wait_shiny(page)
    # Simulate a misconfigured server by delivering a render message whose
    # sandbox URL is the app's own origin.
    page.evaluate(
        """url => {
            const el = document.querySelector("shiny-mcp-widget[data-widget-id='w_err']");
            el.handleServerMessage({v: 1, host_id: el.dataset.hostId, widget_id: 'w_err', kind: 'render',
              mount_id: el.dataset.mountId, payload: {render_id: 1, server: 'fx', tool: 'x',
              resource: {uri: 'ui://x', html: '<p>x</p>', csp: {}, permissions: {}, prefers_border: false},
              tool_input: {arguments: {}}, tool_result: {content: []}, tool_cancelled: null,
              sandbox: {url: url + '/sandbox.html', view_same_origin: false}, host_context: {},
              capabilities: {open_links: false, resource_read: false}}});
        }""",
        fixture_app.url,
    )
    expect(widget(page, "w_err").get_by_role("alert")).to_contain_text("must differ from the app origin")
    expect(widget(page, "w_err").locator("iframe")).to_have_count(0)
    errors.assert_none()
