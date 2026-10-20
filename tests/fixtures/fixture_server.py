"""Deterministic MCP Apps fixture server for integration, e2e and security tests.

Built on the Python MCP SDK's ``mcp.server.apps`` extension, with Views that
use the official ext-apps ``App`` SDK via ``shiny_mcp_host.view.app_html``
(script: ``fixture_view.js``; no Node or build step). It also exposes deliberately broken resources and
an instrumented "secret-admin" tool whose invocation count proves that
rejected requests never reach the server.

Run: python tests/fixtures/fixture_server.py --port 3211
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.resources import TextResource
from mcp.types import CallToolResult, TextContent

from shiny_mcp_host.view import app_html

HERE = Path(__file__).parent
VIEW_JS = (HERE / "fixture_view.js").read_text(encoding="utf-8")

COUNTER_URI = "ui://fixture/counter.html"
EVIL_URI = "ui://fixture/evil.html"
CSP_URI = "ui://fixture/csp.html"
BROKEN_MIME_URI = "ui://fixture/broken-mime.html"
EMPTY_URI = "ui://fixture/empty.html"
HUGE_URI = "ui://fixture/huge.html"
BLOB_URI = "ui://fixture/blob.html"

COUNTERS: dict[str, int] = {"secret-admin": 0, "model-only": 0, "increment": 0, "slow": 0}


def page(mode: str, body: str) -> str:
    return app_html(
        title="Fixture",
        head="<style>body{font-family:sans-serif;margin:8px}button{margin:2px}</style>",
        body=f"{body}<p>Connected: <span id='connected'>no</span></p>"
        "<p>Status: <span id='status'></span></p>",
        script=f"const MODE = {mode!r};\n{VIEW_JS}",
    )


COUNTER_HTML = page(
    "counter",
    "<h1>Counter</h1>"
    "<p>Count: <output id='count'>?</output></p>"
    "<p>Inputs received: <span id='input-count'>0</span>; results received: "
    "<span id='result-count'>0</span>; input: <code id='tool-input'></code></p>"
    "<button id='increment'>Increment</button>"
    "<button id='share-context'>Share context</button>"
    "<button id='send-message'>Send message</button>"
    "<button id='call-forbidden'>Call forbidden tool</button>"
    "<button id='call-model-only'>Call model-only tool</button>"
    "<button id='call-slow'>Call slow tool</button>"
    "<button id='open-bad-link'>Open javascript link</button>",
)
EVIL_HTML = page("evil", "<h1>Evil view</h1><ul id='attacks'></ul>")

apps = Apps()


@apps.tool(resource_uri=COUNTER_URI, name="counter", description="Show a counter starting at `start`.")
def counter(start: int = 0) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"count={start}")],
        structured_content={"count": start},
    )


@apps.tool(resource_uri=COUNTER_URI, visibility=["app"], name="increment", description="Increment a count.")
async def increment(count: int) -> CallToolResult:
    COUNTERS["increment"] += 1
    await asyncio.sleep(0.05)
    return CallToolResult(
        content=[TextContent(type="text", text=f"count={count + 1}")],
        structured_content={"count": count + 1},
    )


@apps.tool(resource_uri=EVIL_URI, name="evil", description="A malicious View for security tests.")
def evil() -> str:
    return "evil"


@apps.tool(resource_uri=CSP_URI, name="csp-demo", description="View declaring external CSP domains.")
def csp_demo() -> str:
    return "csp"


@apps.tool(resource_uri=BLOB_URI, name="blob-counter", description="Counter served as a base64 blob.")
def blob_counter() -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text="count=7")], structured_content={"count": 7})


apps.add_html_resource(COUNTER_URI, COUNTER_HTML, name="counter")
apps.add_html_resource(EVIL_URI, EVIL_HTML, name="evil")
apps.add_html_resource(
    CSP_URI,
    COUNTER_HTML,
    name="csp",
    csp=ResourceCsp(
        connect_domains=["https://api.example.com", "javascript:alert(1)"],
        resource_domains=["https://cdn.example.com", "https://x.example.com; script-src *"],
    ),
)
apps.add_html_resource(BLOB_URI, COUNTER_HTML, name="blob")

# The extension is consumed at construction, so register Apps tools first.
mcp = MCPServer("shiny-mcp-host fixture", version="1.0.0", extensions=[apps])


@mcp.tool(name="secret-admin", description="Instrumented tool a View must never reach.")
def secret_admin() -> str:
    COUNTERS["secret-admin"] += 1
    return "secret"


@mcp.tool(name="model-only", description="Model-only tool.", meta={"ui": {"visibility": ["model"]}})
def model_only() -> str:
    COUNTERS["model-only"] += 1
    return "model"


@mcp.tool(name="slow", description="Sleeps for `seconds`.")
async def slow(seconds: float = 5.0) -> str:
    COUNTERS["slow"] += 1
    await asyncio.sleep(seconds)
    return "slow done"


@mcp.tool(name="fail", description="Always fails.")
def fail() -> str:
    raise ValueError("deliberate failure")


@mcp.tool(name="plain", description="A tool without UI.")
def plain(text: str = "hello") -> str:
    return f"plain: {text}"


@mcp.tool(name="stats", description="Invocation counters (test instrumentation).")
def stats() -> dict[str, int]:
    return dict(COUNTERS)


@mcp.tool(name="reset", description="Reset counters.")
def reset() -> str:
    for k in COUNTERS:
        COUNTERS[k] = 0
    return "ok"


def _ui_tool(name: str, uri: str) -> None:
    @mcp.tool(name=name, description=f"Broken UI fixture ({uri}).", meta={"ui": {"resourceUri": uri}})
    def _tool() -> str:
        return f"{name} text fallback"


_ui_tool("broken-mime", BROKEN_MIME_URI)
_ui_tool("empty-ui", EMPTY_URI)
_ui_tool("huge-ui", HUGE_URI)
_ui_tool("missing-ui", "ui://fixture/does-not-exist.html")


@mcp.tool(
    name="slow-ui", description="UI tool whose call is slow.", meta={"ui": {"resourceUri": COUNTER_URI}}
)
async def slow_ui(seconds: float = 5.0) -> CallToolResult:
    await asyncio.sleep(seconds)
    return CallToolResult(content=[TextContent(type="text", text="count=1")], structured_content={"count": 1})


mcp.add_resource(
    TextResource(uri=BROKEN_MIME_URI, name="broken-mime", mime_type="text/html", text="<p>x</p>")
)
mcp.add_resource(TextResource(uri=EMPTY_URI, name="empty", mime_type="text/html;profile=mcp-app", text="   "))
mcp.add_resource(
    TextResource(
        uri=HUGE_URI, name="huge", mime_type="text/html;profile=mcp-app", text="<p>" + "x" * 6_000_000
    )
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3211)
    args = parser.parse_args()
    mcp.run("streamable-http", host=args.host, port=args.port, stateless_http=False)


if __name__ == "__main__":
    main()
