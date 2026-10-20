"""Trusted mode (``dangerously_trust_apps=True``) in a real browser.

There is no sandbox origin and no proxy: each View is one opaque-origin
``srcdoc`` iframe on the Shiny page. These tests pin down what that mode still
guarantees, using the same fixture Views as the sandboxed suites.
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from tests.e2e.helpers import cmd, outer_frame, wait_ready, widget
from tests.e2e.test_security import ATTACKS, FIRE_AND_FORGET, fixture_stats
from tests.fixtures.fixture_server import CSP_PROBE_URL

pytestmark = [pytest.mark.e2e, pytest.mark.security]


def trusted_view(page, widget_id):
    """In trusted mode the widget's only iframe is the View itself."""
    return outer_frame(page, widget_id)


def test_trusted_view_round_trip_without_sandbox(trusted_fixture_app, page_with_errors):
    page, errors = page_with_errors
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.goto(trusted_fixture_app.url)
    cmd(page, action="render", tool="counter", target="w_a", args={"start": 41})
    wait_ready(page, "w_a")
    frame = widget(page, "w_a").locator("iframe")
    assert frame.get_attribute("sandbox") == "allow-scripts allow-forms"
    assert frame.get_attribute("src") is None
    v = trusted_view(page, "w_a")
    expect(v.locator("#count")).to_have_text("41")
    page.wait_for_timeout(300)
    expect(v.locator("#input-count")).to_have_text("1")
    expect(v.locator("#result-count")).to_have_text("1")
    # A View tool call and a model-context update still go through Python.
    v.get_by_role("button", name="Increment").click()
    expect(v.locator("#count")).to_have_text("42")
    v.get_by_role("button", name="Share context").click()
    expect(page.locator("#contexts")).to_contain_text('"count": 42')
    # The sandbox server set up by the harness was never contacted.
    assert not [u for u in requests if "sandbox.html" in u]
    errors.assert_none()


def test_trusted_view_csp_is_enforced_by_meta_tag(trusted_fixture_app, page_with_errors):
    page, errors = page_with_errors
    page.goto(trusted_fixture_app.url)
    cmd(page, action="render", tool="counter", target="w_a", args={"start": 1})
    wait_ready(page, "w_a")
    srcdoc = widget(page, "w_a").locator("iframe").get_attribute("srcdoc") or ""
    assert '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'' in srcdoc
    assert "frame-ancestors" not in srcdoc
    errors.assert_none()


@pytest.mark.parametrize("tool", ["csp-probe-comment", "csp-probe-script"])
def test_trusted_csp_applies_to_scripts_before_the_views_head(trusted_fixture_app, page_with_errors, tool):
    """The policy is the host's own document start, not found by searching the View's HTML."""
    page, errors = page_with_errors
    leaks = []
    page.on("request", lambda r: leaks.append(r.url) if r.url.startswith(CSP_PROBE_URL) else None)
    page.goto(trusted_fixture_app.url)
    cmd(page, action="render", tool=tool, target="w_a")
    wait_ready(page, "w_a")
    v = trusted_view(page, "w_a")
    expect(v.locator("html")).to_have_attribute("data-probe", "blocked:connect-src")
    expect(v.locator("#count")).to_have_text("1")
    assert leaks == []
    errors.assert_none()


def test_malicious_view_in_trusted_mode(trusted_fixture_app, fixture_server, page_with_errors):
    """Trusted mode is not a promise, but in practice it still contains a hostile View."""
    page, errors = page_with_errors
    page.context.add_cookies([{"name": "shiny_secret", "value": "s3cret", "url": trusted_fixture_app.url}])
    page.goto(trusted_fixture_app.url)
    before = fixture_stats(fixture_server)["secret-admin"]
    cmd(page, action="render", tool="evil", target="w_evil")
    wait_ready(page, "w_evil")
    v = trusted_view(page, "w_evil")
    expect(v.locator("#status")).to_have_text("attacks done", timeout=20_000)
    results = {
        li.get_attribute("data-attack"): (li.get_attribute("data-blocked"), li.inner_text())
        for li in v.locator("#attacks li").all()
    }
    assert set(results) == set(ATTACKS)
    for name in ATTACKS:
        if name not in FIRE_AND_FORGET:
            assert results[name][0] == "true", f"attack not blocked: {results[name][1]}"
    assert "s3cret" not in json.dumps(results)
    assert page.url.startswith(trusted_fixture_app.url)
    assert len(page.context.pages) == 1
    expect(page.locator("#pwned")).to_have_text("pwned=None")
    expect(v.locator("h1")).to_have_text("Evil view")
    page.wait_for_timeout(500)
    # The forged tools/call reached the host as an ordinary View request
    # (there is no proxy to tell them apart) and HostPolicy rejected it.
    assert fixture_stats(fixture_server)["secret-admin"] == before
    errors.assert_none()
