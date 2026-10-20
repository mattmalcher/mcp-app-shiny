"""Check a deployed sales explorer (Shiny app + sandbox + MCP server) end to end.

    python scripts/check_deployment.py --app-url URL --sandbox-url URL [--mcp-url URL] [options]
    python scripts/check_deployment.py --app-url URL --trusted [--mcp-url URL] [options]

Runs, in order, and reports each check as PASS, FAIL or INCONCLUSIVE:

1. server   : the sandbox from the server side (shiny_mcp_host.sandbox.check_sandbox):
              reachability, redirects, TLS, frame-ancestors / X-Frame-Options.
              Optionally the MCP server: reachable with MCP_API_KEY; and, with a key,
              an unauthenticated request must get 401/403 or a login redirect (any
              other answer, e.g. a timeout or 5xx, is INCONCLUSIVE, not a pass).
2. browser  : in Chromium, the Shiny app loads, the explorer renders in a chat reply
              (widget data-state="ready"), a filter change re-queries through Python,
              a region selection reaches Python, and the follow-up chat message is
              answered from that selection.
3. isolation: the View's frames are on the sandbox origin with the expected sandbox
              attributes, cannot reach the Shiny page or its cookie, cannot fetch, and
              a tool call outside the policy is refused.

With --trusted (the app runs with SHINY_MCP_DANGEROUSLY_TRUST_APPS=1, no sandbox):
the server stage reports whether the app page sends a Content-Security-Policy, which
the View's srcdoc iframe inherits (INCONCLUSIVE if present: the browser run decides);
the isolation stage checks the single View iframe is opaque-origin, has no
allow-same-origin, and is still contained, as above.

Server checks passing while browser checks fail points at the browser side (frame
headers seen only by browsers, mixed content, login redirects inside the frame).
Writes evidence (sanitized headers, versions, results, screenshots) to --evidence-dir.
Exit code 0 only if every check passed; INCONCLUSIVE counts as not passed. The evidence
file is written even when a stage crashes (the crash is recorded as a failed check)
or the run is interrupted ("completed": false, never "passed": true).
Credentials are read from the environment (APP_API_KEY, MCP_API_KEY) and never
written to the evidence.

Needs the dev environment (`make setup-dev`: Playwright + Chromium); no Node.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import sys
import time
import traceback
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit

from shiny_mcp_host import __version__
from shiny_mcp_host.sandbox import check_sandbox

QUESTION = "Explore Q3 sales in the West"
SAFE_HEADERS = {
    "content-security-policy",
    "x-frame-options",
    "location",
    "content-type",
    "cache-control",
    "strict-transport-security",
    "server",
    "via",
    "x-content-type-options",
    "referrer-policy",
    "cross-origin-opener-policy",
    "cross-origin-embedder-policy",
    "cross-origin-resource-policy",
    "permissions-policy",
}


@dataclass
class Check:
    stage: str
    name: str
    ok: bool
    detail: str
    #: Could not decide (counts as not passed): e.g. a timeout where an auth refusal was expected.
    inconclusive: bool = False


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, stage: str, name: str, ok: bool, detail: str, *, inconclusive: bool = False) -> bool:
        self.checks.append(Check(stage, name, ok, detail, inconclusive))
        label = "PASS" if ok else "INCONCLUSIVE" if inconclusive else "FAIL"
        print(f"{label}  [{stage}] {name}: {detail}", flush=True)
        return ok

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c.ok for c in self.checks)


def origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def sanitized_headers(url: str) -> dict[str, Any]:
    """Status and security-relevant headers of one GET, without following redirects."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a: Any, **k: Any) -> None:
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(url, timeout=15) as r:
            status, headers = r.status, r.headers
    except HTTPError as e:
        status, headers = e.code, e.headers
    except (URLError, OSError) as e:
        return {"error": str(e)}
    picked: dict[str, list[str]] = {}
    for key in sorted({k.lower() for k in headers.keys()} & SAFE_HEADERS):
        picked[key] = headers.get_all(key) or []
    if "location" in picked:  # may carry a login redirect with tokens in the query
        picked["location"] = [re.sub(r"\?.*", "?…", v) for v in picked["location"]]
    return {"status": status, "headers": picked}


# --------------------------------------------------------------- server side


def check_server_side(report: Report, args: argparse.Namespace, evidence: dict[str, Any]) -> None:
    app_origin = origin(args.app_url)
    evidence["app_response"] = sanitized_headers(args.app_url)
    if args.trusted:
        check_app_page_csp(report, evidence["app_response"])
    else:
        for c in check_sandbox(args.sandbox_url, app_origin):
            report.add("server", f"sandbox {c.name}", c.ok, c.detail, inconclusive=c.inconclusive)
        page = f"{args.sandbox_url}?{urlencode({'host': app_origin, 'nonce': 'x' * 32})}"
        evidence["sandbox_response"] = sanitized_headers(page)
    if args.mcp_url:
        check_mcp(report, args.mcp_url, os.environ.get("MCP_API_KEY"))


def check_app_page_csp(report: Report, response: dict[str, Any]) -> None:
    """Trusted mode: Views are srcdoc iframes, which inherit the app page's CSP."""
    if "error" in response or response.get("status") != 200:
        detail = response.get("error") or f"HTTP {response.get('status')}"
        report.add("server", "app page CSP", False, f"app page not fetched ({detail})", inconclusive=True)
        return
    policies = response["headers"].get("content-security-policy", [])
    if not policies:
        report.add("server", "app page CSP", True, "none sent; Views get only their own policy")
        return
    report.add(
        "server",
        "app page CSP",
        False,
        f"Views inherit {policies!r}; inline View scripts need 'unsafe-inline' in script-src",
        inconclusive=True,
    )


def check_mcp(report: Report, url: str, key: str | None) -> None:
    from shiny_mcp_host.client import ServerConfig, ServerConnection

    async def tools(headers: dict[str, str] | None) -> list[str]:
        conn = ServerConnection(ServerConfig("sales", url=url, headers=headers), timeout=20)
        try:
            return sorted(t.name for t in await conn.list_tools())
        finally:
            await conn.close()

    try:
        names = asyncio.run(tools({"Authorization": f"Key {key}"} if key else None))
        report.add("server", "mcp reachable", "explore_sales" in names, f"tools: {names}")
    except Exception as e:  # noqa: BLE001 - reported
        report.add("server", "mcp reachable", False, f"{type(e).__name__}: {e}")
        return
    if key:
        verdict, detail = classify_unauthenticated(*post_without_credentials(url))
        report.add(
            "server",
            "mcp requires auth",
            verdict == "rejected",
            detail,
            inconclusive=verdict == "inconclusive",
        )


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "check_deployment", "version": __version__},
    },
}


def post_without_credentials(url: str) -> tuple[int | None, str | None, str | None]:
    """POST an MCP ``initialize`` with no credentials: (status, Location, network error)."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a: Any, **k: Any) -> None:
            return None

    request = urllib.request.Request(
        url,
        data=json.dumps(INITIALIZE).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as r:
            return r.status, None, None
    except HTTPError as e:
        return e.code, e.headers.get("Location") if e.headers else None, None
    except (URLError, OSError) as e:
        return None, None, f"{type(e).__name__}: {getattr(e, 'reason', e)}"


def classify_unauthenticated(status: int | None, location: str | None, error: str | None) -> tuple[str, str]:
    """Classify a credential-less MCP request: ``rejected``, ``served`` or ``inconclusive``.

    Only an explicit authentication refusal (401/403, or a redirect to a login
    page) counts as evidence that the server requires credentials. A success
    means it does not; anything else (timeouts, 5xx, other redirects or client
    errors) says nothing about authentication.
    """
    if error is not None:
        return (
            "inconclusive",
            f"no HTTP answer without credentials ({error}); cannot tell if auth is enforced",
        )
    if status in (401, 403):
        return "rejected", f"HTTP {status} without credentials"
    login = location is not None and re.search(r"__login__|/login|/connect/#/login", location) is not None
    if status is not None and 300 <= status < 400 and login:
        return "rejected", f"HTTP {status} redirect to a login page without credentials"
    if status is not None and 200 <= status < 300:
        return "served", f"HTTP {status}: the MCP server answered without credentials"
    where = f" to {re.sub(r'[?#].*', '', location)}" if location else ""
    return "inconclusive", f"HTTP {status}{where} without credentials is not an authentication refusal"


# ------------------------------------------------------------------ browser


def check_browser(report: Report, args: argparse.Namespace, evidence: dict[str, Any], shots: Path) -> None:
    from playwright.sync_api import TimeoutError as PWTimeout
    from playwright.sync_api import expect, sync_playwright

    app_origin = origin(args.app_url)
    sandbox_origin = None if args.trusted else origin(args.sandbox_url)
    app_key = os.environ.get("APP_API_KEY")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        evidence["browser"] = f"chromium {browser.version}"
        context = browser.new_context(
            viewport={"width": 1280, "height": 1000},
            storage_state=str(args.storage_state) if args.storage_state else None,
        )
        if app_key:
            # Only requests to the app's origin carry the key; never the sandbox hostname.
            def add_key(route: Any) -> None:
                if origin(route.request.url) == app_origin:
                    route.continue_(headers={**route.request.headers, "authorization": f"Key {app_key}"})
                else:
                    route.continue_()

            context.route("**/*", add_key)
        page = context.new_page()
        page.set_default_timeout(20_000)
        console: list[str] = []
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"[:500]))
        page.on("pageerror", lambda e: console.append(f"pageerror: {e}"[:500]))
        try:
            _browser_flow(report, page, args, app_origin, sandbox_origin, expect, PWTimeout)
        finally:
            try:
                page.screenshot(path=str(shots / "final.png"), full_page=True)
            except Exception:  # noqa: BLE001
                pass
            evidence["console"] = [
                c for c in console if "[shiny-mcp" in c or "error" in c.lower() or "refused" in c.lower()
            ][:100]
            context.close()
            browser.close()


def _browser_flow(report, page, args, app_origin, sandbox_origin, expect, PWTimeout) -> None:
    page.goto(args.app_url)
    if origin(page.url) != app_origin or "__login__" in page.url:
        report.add(
            "browser", "app loads", False, f"ended at {re.sub(r'[?#].*', '', page.url)} (login required?)"
        )
        return
    try:
        page.wait_for_function("() => window.Shiny && Shiny.shinyapp && Shiny.shinyapp.isConnected()")
    except PWTimeout:
        report.add("browser", "app loads", False, "Shiny did not connect (websocket blocked or auth?)")
        return
    report.add("browser", "app loads", True, f"Shiny connected at {app_origin}")

    box = page.get_by_role("textbox", name="Chat message")
    box.fill(QUESTION)
    page.keyboard.press("Enter")
    w = page.locator(".shiny-chat-message shiny-mcp-widget").first
    try:
        expect(w).to_have_attribute("data-state", re.compile("ready|error|fallback"), timeout=45_000)
    except AssertionError:
        report.add("browser", "explorer renders", False, f"data-state={w.get_attribute('data-state')!r}")
        return
    state = w.get_attribute("data-state")
    if state != "ready":
        alert = w.get_by_role("alert")
        text = alert.inner_text() if alert.count() else ""
        report.add(
            "browser",
            "explorer renders",
            False,
            f"data-state={state} data-error-code={w.get_attribute('data-error-code')}: {text[:400]}",
        )
        return
    report.add("browser", "explorer renders", True, 'widget data-state="ready" (View initialized)')

    outer_el = w.locator("iframe.shiny-mcp-widget-frame")
    outer = outer_el.element_handle().content_frame()
    if sandbox_origin is None:  # trusted mode: the widget's iframe is the View itself
        check_trusted_isolation(report, page, outer_el, outer, app_origin)
        v = w.frame_locator("iframe.shiny-mcp-widget-frame")
    else:
        inner = outer.locator("iframe").element_handle().content_frame()
        check_isolation(report, page, outer_el, outer, inner, app_origin, sandbox_origin)
        v = w.frame_locator("iframe.shiny-mcp-widget-frame").frame_locator("iframe")
    total = v.locator("#total").inner_text()
    v.get_by_label("Product").select_option("Gadgets")
    try:
        expect(v.locator("#total")).not_to_have_text(total)
        report.add(
            "browser", "filter re-queries", True, f"total {total} -> {v.locator('#total').inner_text()}"
        )
    except AssertionError:
        report.add("browser", "filter re-queries", False, "total did not change after Product=Gadgets")
        return
    v.get_by_role("button", name="South:", exact=False).click()
    try:
        expect(page.locator("#selection")).to_contain_text('"region": "South"')
        sel = json.loads(page.locator("#selection").inner_text())["selection"]
        report.add("browser", "selection reaches Python", True, f"selection {sel}")
    except (AssertionError, ValueError, KeyError) as e:
        report.add("browser", "selection reaches Python", False, f"sidebar did not show South ({e})")
        return
    v.get_by_role("button", name="Ask about this region").click()
    answer = page.locator(".shiny-chat-message").last
    try:
        expect(page.locator(".shiny-chat-user-message").last).to_contain_text(
            "What is driving sales in South"
        )
        expect(answer).to_contain_text("South brought in")
        expect(answer).to_contain_text(f"${sel['revenue']:,}")
        report.add("browser", "follow-up answered", True, answer.inner_text().splitlines()[0][:200])
    except AssertionError:
        report.add("browser", "follow-up answered", False, f"last message: {answer.inner_text()[:300]!r}")


INNER_PROBES = """async ({appOrigin}) => {
  const out = {};
  try { out.parentDocument = !!window.parent.document; } catch (e) { out.parentDocument = false; }
  try { out.topDocument = !!window.top.document; } catch (e) { out.topDocument = false; }
  try { out.cookie = document.cookie; } catch (e) { out.cookie = null; }
  try { await fetch(appOrigin, {mode: "no-cors"}); out.fetch = true; } catch (e) { out.fetch = false; }
  out.forbiddenTool = await new Promise((resolve) => {
    const id = 990001;
    const timer = setTimeout(() => resolve({timeout: true}), 15000);
    addEventListener("message", (e) => {
      if (e.data && e.data.id === id) { clearTimeout(timer); resolve(e.data); }
    });
    window.parent.postMessage(
      {jsonrpc: "2.0", id, method: "tools/call", params: {name: "__not_allowed__", arguments: {}}}, "*");
  });
  out.origin = self.origin;
  return out;
}"""


def check_isolation(report, page, outer_el, outer, inner, app_origin, sandbox_origin) -> None:
    outer_sandbox = outer_el.get_attribute("sandbox")
    report.add(
        "isolation",
        "outer frame",
        origin(outer.url) == sandbox_origin and sandbox_origin != app_origin,
        f"origin {origin(outer.url)} (app {app_origin}), sandbox={outer_sandbox!r}",
    )
    inner_sandbox = outer.locator("iframe").get_attribute("sandbox") or ""
    report.add(
        "isolation",
        "inner frame",
        "allow-same-origin" not in inner_sandbox and "allow-scripts" in inner_sandbox,
        f"sandbox={inner_sandbox!r}",
    )
    check_view_contained(report, page, inner, app_origin)


def check_trusted_isolation(report, page, frame_el, view, app_origin) -> None:
    sandbox = frame_el.get_attribute("sandbox") or ""
    report.add(
        "isolation",
        "View frame",
        sandbox.split() == ["allow-scripts", "allow-forms"] and frame_el.get_attribute("src") is None,
        f"trusted mode: one srcdoc frame, sandbox={sandbox!r}",
    )
    check_view_contained(report, page, view, app_origin)


def check_view_contained(report, page, view, app_origin) -> None:
    page.context.add_cookies([{"name": "deploy_check_secret", "value": "s3cret", "url": page.url}])
    probes = view.evaluate(INNER_PROBES, {"appOrigin": app_origin})
    report.add(
        "isolation",
        "View cannot reach the Shiny page",
        not probes["parentDocument"]
        and not probes["topDocument"]
        and "s3cret" not in (probes["cookie"] or "")
        and probes["origin"] == "null",
        f"parent.document={probes['parentDocument']}, top.document={probes['topDocument']}, "
        f"opaque origin={probes['origin']}",
    )
    report.add("isolation", "View cannot fetch", probes["fetch"] is False, f"fetch to {app_origin} blocked")
    reply = probes["forbiddenTool"]
    refused = isinstance(reply, dict) and ("error" in reply or (reply.get("result") or {}).get("isError"))
    report.add("isolation", "unauthorised tool call refused", bool(refused), json.dumps(reply)[:300])


# --------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--app-url", required=True, help="the Shiny app, e.g. https://connect.example.com/content/<guid>/"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sandbox-url", help="the sandbox page on its own hostname (…/sandbox.html)")
    mode.add_argument(
        "--trusted",
        action="store_true",
        help="the app runs in trusted mode (SHINY_MCP_DANGEROUSLY_TRUST_APPS=1): no sandbox",
    )
    parser.add_argument("--mcp-url", help="the MCP endpoint (…/mcp), to check it directly (optional)")
    parser.add_argument(
        "--storage-state",
        type=Path,
        help="Playwright storage state with a login session for the app (optional)",
    )
    parser.add_argument("--evidence-dir", type=Path, default=Path("test-results/deployment-check"))
    parser.add_argument("--skip-browser", action="store_true", help="server-side checks only")
    args = parser.parse_args()

    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    report = Report()
    evidence: dict[str, Any] = {
        "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app_url": args.app_url,
        "sandbox_url": args.sandbox_url,
        "mode": "trusted" if args.trusted else "sandboxed",
        "mcp_url": args.mcp_url,
        "checker": {
            "shiny_mcp_host": __version__,
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "credentials": {
            "APP_API_KEY": bool(os.environ.get("APP_API_KEY")),
            "MCP_API_KEY": bool(os.environ.get("MCP_API_KEY")),
        },
    }
    completed = False
    try:
        run_stage(report, evidence, "server", lambda: check_server_side(report, args, evidence))
        if not args.skip_browser:
            run_stage(
                report,
                evidence,
                "browser",
                lambda: check_browser(report, args, evidence, args.evidence_dir),
            )
        completed = True
    finally:
        # Failed deployments are when this report matters most: always write it.
        evidence["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        evidence["checks"] = [asdict(c) for c in report.checks]
        evidence["completed"] = completed  # False if interrupted: a partial run never passes
        evidence["passed"] = report.ok and completed
        path = args.evidence_dir / "evidence.json"
        path.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    print(f"\n{'ALL CHECKS PASSED' if report.ok else 'SOME CHECKS DID NOT PASS'}; evidence in {path}")
    sys.exit(0 if report.ok else 1)


def run_stage(report: Report, evidence: dict[str, Any], stage: str, run: Callable[[], None]) -> None:
    """Run one stage; an unexpected exception becomes a failed check with its traceback in the evidence."""
    try:
        run()
    except Exception as e:  # noqa: BLE001 - recorded, and the run continues to the evidence file
        report.add(stage, "unexpected error", False, f"{type(e).__name__}: {str(e)[:500]}")
        evidence.setdefault("errors", []).append(
            {"stage": stage, "traceback": traceback.format_exc()[-4000:]}
        )


if __name__ == "__main__":
    main()
