"""Shiny in Shiny: an MCP server whose MCP App View is itself a Shiny for Python app.

Tools:
- ``explore_dataset`` (UI): opens the inner Shiny app (inner_app.py) on iris or cars.
- ``set_plot_controls`` (model): moves the controls of an open plot.
- ``shiny_rpc`` (app-only): the inner app's "websocket", carried by tool calls (shiny_view.py).

Run locally:  python examples/shiny_in_shiny/server.py --port 3001

The inner Shiny sessions live in this process, so run a single worker and keep
the server stateful (a plain `mcp.run("streamable-http")`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent))

from inner_app import DATASETS, NONE, valid_controls  # noqa: E402
from inner_app import app as inner_app  # noqa: E402
from inner_app import app_ui as inner_ui  # noqa: E402
from mcp.server.apps import Apps  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402
from shiny_view import ShinyView, view  # noqa: E402

VIEW_URI = "ui://shiny-in-shiny/view.html"
shiny_view = ShinyView(inner_app, inner_ui, rpc_tool="shiny_rpc", title="Shiny in Shiny")
apps = Apps()


def _describe(c: dict[str, str]) -> str:
    colour = f", coloured by {c['color']}" if c["color"] != NONE else ""
    return f"Scatter plot of {c['dataset']}: {c['y']} against {c['x']}{colour}."


@apps.tool(
    resource_uri=VIEW_URI,
    name="explore_dataset",
    description=(
        "Open an interactive scatter plot (a Shiny app) of a toy dataset. "
        f"iris columns: {', '.join(DATASETS['iris']['numeric'])}; colour by species. "
        f"cars columns: {', '.join(DATASETS['cars']['numeric'])}; colour by origin or cylinders. "
        "Use color='none' for no colouring."
    ),
)
def explore_dataset(
    dataset: Literal["iris", "cars"] = "iris",
    x: str | None = None,
    y: str | None = None,
    color: str | None = None,
) -> CallToolResult:
    c = valid_controls({"dataset": dataset, "x": x, "y": y, "color": color})
    return CallToolResult(content=[TextContent(type="text", text=_describe(c))], structured_content=c)


@apps.tool(
    resource_uri=VIEW_URI,
    name="set_plot_controls",
    description=(
        "Change the controls of an open explore_dataset plot. `session` is the plot's session id, "
        "from the model context the plot shares. Omitted controls stay as they are."
    ),
)
async def set_plot_controls(
    session: str,
    dataset: Literal["iris", "cars"] | None = None,
    x: str | None = None,
    y: str | None = None,
    color: str | None = None,
) -> CallToolResult:
    requested = {k: v for k, v in {"dataset": dataset, "x": x, "y": y, "color": color}.items() if v}
    try:
        await shiny_view.run_in(session, lambda s: view(s).tool_input.set(requested))
    except KeyError:
        return CallToolResult(
            content=[TextContent(type="text", text="That plot is no longer open.")], is_error=True
        )
    return CallToolResult(content=[TextContent(type="text", text=f"Requested {requested}.")])


@apps.tool(
    resource_uri=VIEW_URI,
    visibility=["app"],
    name="shiny_rpc",
    description="Transport for the inner Shiny app (called by the View only).",
)
async def shiny_rpc(
    op: Literal["open", "send", "poll", "close"],
    sid: str | None = None,
    msgs: list[str] | None = None,
    tool_input: dict | None = None,
) -> CallToolResult:
    result = await shiny_view.handle(op, sid, msgs, tool_input)
    return CallToolResult(content=[TextContent(type="text", text=op)], structured_content=result)


apps.add_html_resource(VIEW_URI, shiny_view.page_html(), name="Shiny in Shiny", prefers_border=True)
mcp = MCPServer("Shiny in Shiny", version="0.1.0", extensions=[apps])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3001)
    args = parser.parse_args()
    mcp.run("streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
