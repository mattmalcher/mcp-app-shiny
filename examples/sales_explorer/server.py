"""Sales Explorer: an MCP server with an interactive MCP App View.

Tools:
- ``explore_sales`` (UI): opens the explorer, optionally pre-filtered.
- ``query_sales`` (app-only): the View calls it through the host when filters change.

Data is synthetic and deterministic.

Run locally:      python examples/sales_explorer/server.py --port 3001
Hosted (ASGI):    the module-level ``app`` (e.g. on Posit Connect: entry point ``server:app``).
                  Set MCP_ALLOWED_HOSTS (comma-separated Host header values, e.g.
                  ``connect.example.com``) to enable DNS-rebinding protection.

The View is plain HTML/CSS/JS (view.*) using the official MCP Apps SDK that
shiny-mcp-host bundles (``shiny_mcp_host.view.app_html``); no Node or build step.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent

HERE = Path(__file__).parent
VIEW_URI = "ui://sales-explorer/view.html"
REGIONS = ["North", "South", "East", "West"]
PRODUCTS = ["Widgets", "Gadgets", "Gizmos", "Doohickeys"]
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]


def _rows() -> list[dict]:
    rows = []
    for r, region in enumerate(REGIONS):
        for p, product in enumerate(PRODUCTS):
            for q, quarter in enumerate(QUARTERS):
                units = 120 + 37 * r + 53 * p + 29 * q + (r * p * 17 + q * r * 11) % 90
                price = [19, 45, 72, 9][p]
                rows.append(
                    {
                        "region": region,
                        "product": product,
                        "quarter": quarter,
                        "units": units,
                        "revenue": units * price,
                    }
                )
    return rows


ROWS = _rows()


def query(region: str | None = None, product: str | None = None, quarter: str | None = None) -> dict:
    """Filter the dataset. Empty / "All" filters match everything."""

    def ok(value: str, wanted: str | None) -> bool:
        return not wanted or wanted == "All" or value == wanted

    rows = [
        r for r in ROWS if ok(r["region"], region) and ok(r["product"], product) and ok(r["quarter"], quarter)
    ]
    return {
        "filters": {"region": region or "All", "product": product or "All", "quarter": quarter or "All"},
        "regions": REGIONS,
        "products": PRODUCTS,
        "quarters": QUARTERS,
        "rows": rows,
        "total_revenue": sum(r["revenue"] for r in rows),
        "total_units": sum(r["units"] for r in rows),
    }


def _result(data: dict) -> CallToolResult:
    f = data["filters"]
    text = (
        f"Sales for region={f['region']}, product={f['product']}, quarter={f['quarter']}: "
        f"${data['total_revenue']:,} revenue, {data['total_units']:,} units across {len(data['rows'])} rows."
    )
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=data)


def view_html() -> str:
    """The View: view.html/.css/.js plus the official MCP Apps SDK, as one document."""
    prebuilt = HERE / "view.built.html"  # written by scripts/package_demo_server.py
    if prebuilt.exists():
        return prebuilt.read_text(encoding="utf-8")
    from shiny_mcp_host.view import app_html

    return app_html(
        title="Sales explorer",
        head=f"<style>{(HERE / 'view.css').read_text(encoding='utf-8')}</style>",
        body=(HERE / "view.html").read_text(encoding="utf-8"),
        script=(HERE / "view.js").read_text(encoding="utf-8"),
    )


apps = Apps()


@apps.tool(resource_uri=VIEW_URI, name="explore_sales", description="Open the interactive sales explorer.")
def explore_sales(
    region: str | None = None, product: str | None = None, quarter: str | None = None
) -> CallToolResult:
    # `region` pre-selects a region in the View; the chart still shows them all.
    data = query(None, product, quarter)
    if region in REGIONS:
        data["filters"]["region"] = region
    return _result(data)


@apps.tool(
    resource_uri=VIEW_URI,
    visibility=["app"],
    name="query_sales",
    description="Filter sales data (called by the explorer View).",
)
def query_sales(
    region: str | None = None, product: str | None = None, quarter: str | None = None
) -> CallToolResult:
    return _result(query(region, product, quarter))


apps.add_html_resource(VIEW_URI, view_html(), name="Sales explorer", prefers_border=True)
mcp = MCPServer("Sales Explorer", version="1.0.0", extensions=[apps])


def asgi_app():
    """Streamable HTTP endpoint at ``/mcp``, stateless so any worker can serve any request."""
    hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = (
        TransportSecuritySettings(allowed_hosts=hosts)
        if hosts
        else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    return mcp.streamable_http_app(stateless_http=True, transport_security=security, host="0.0.0.0")


app = asgi_app()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3001)
    args = parser.parse_args()
    mcp.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
