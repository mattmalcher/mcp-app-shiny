"""Sales explorer: an interactive MCP App in ui.Chat, with a round trip back to Python.

1. Ask "show me sales" -> the "agent" calls the explore_sales tool and the
   interactive explorer is rendered inside its chat reply (render_in_chat: the
   tool is not called twice).
2. Change filters in the explorer -> the View calls query_sales through Python.
3. Click a region -> the View shares the selection as model context; the
   sidebar shows what Python received.
4. Click "Ask about this region" -> the View sends a chat message; Python
   answers using the selection and another tool call.

No LLM is needed: `plan()` below stands in for one. Run everything with `make demo`.

Configuration (environment variables; all optional locally):

- MCP_SERVER_URL         the sales MCP server (default http://127.0.0.1:3001/mcp)
- MCP_SERVER_API_KEY     sent as "Authorization: Key <value>" (e.g. a Posit Connect API key);
                         it stays in Python and never reaches the browser
- SHINY_MCP_SANDBOX_URL  a deployed sandbox (.../sandbox.html) on its own hostname; when unset,
                         a development sandbox is started on http://localhost:SANDBOX_PORT (8765)

docs/deployment.md deploys this file unchanged.
"""

from __future__ import annotations

import json
import os
import re

from shiny import App, reactive, render, ui

from shiny_mcp_host import MCPHost, MCPHostError
from shiny_mcp_host.sandbox import start_sandbox_server

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:3001/mcp")
MCP_HEADERS = {"Authorization": f"Key {key}"} if (key := os.environ.get("MCP_SERVER_API_KEY")) else None
# A deployed sandbox on its own hostname, or a local development one.
SANDBOX_URL = (
    os.environ.get("SHINY_MCP_SANDBOX_URL")
    or start_sandbox_server(port=int(os.environ.get("SANDBOX_PORT", "8765"))).url
)

REGIONS = ["North", "South", "East", "West"]
PRODUCTS = ["Widgets", "Gadgets", "Gizmos", "Doohickeys"]

GREETING = ui.HTML(
    "I can open an interactive sales explorer. Try "
    "<span class='suggestion submit'>Show me sales by region</span> or "
    "<span class='suggestion submit'>Explore Q3 sales in the West</span>."
)


def plan(text: str) -> dict | None:
    """Stand-in for an LLM deciding to call explore_sales, and with which arguments."""
    lower = text.lower()
    if not any(w in lower for w in ("sales", "revenue", "explore", "region")):
        return None
    args: dict[str, str] = {}
    if region := next((r for r in REGIONS if r.lower() in lower), None):
        args["region"] = region
    if product := next((p for p in PRODUCTS if p.lower() in lower), None):
        args["product"] = product
    if m := re.search(r"\bq([1-4])\b", lower):
        args["quarter"] = f"Q{m.group(1)}"
    return args


app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.h5("What Python received"),
        ui.p("Selections the explorer shares via model context:", class_="text-muted small"),
        ui.output_code("selection"),
        ui.p(
            "The explorer runs in a sandboxed iframe; every tool call it makes goes through Python.",
            class_="text-muted small",
        ),
        width=320,
    ),
    ui.chat_ui("chat", greeting=GREETING, drawer=False, show_history=False),
    title="Sales explorer · MCP Apps in Shiny",
    fillable=True,
)


def server(input, output, session):
    chat = ui.Chat(id="chat", history=False)
    host = MCPHost(session, sandbox_url=SANDBOX_URL)
    host.add_server("sales", url=MCP_SERVER_URL, headers=MCP_HEADERS)
    question = reactive.value(None)
    latest = reactive.value(None)

    @host.on_model_context
    def _(event):
        latest.set(None if event.cleared else event)

    @chat.on_user_submit
    async def _(user_input: str):
        args = plan(user_input)
        if args is None:
            await chat.append_message("Ask me about sales, for example *show me sales by region*.")
            return
        # Your agent loop makes the tool call...
        try:
            result = await host.call_tool("sales", "explore_sales", args)
        except MCPHostError as e:
            await chat.append_message(f"Sorry, the sales tool failed: {e}")
            return
        # ...and the host renders the View for that result, without calling again.
        out = await host.render_in_chat(
            chat, "sales", "explore_sales", args, result, text="Here's the explorer:", height=480
        )
        if out.render_error:
            await chat.append_message(f"Sorry, I couldn't show the explorer: {out.render_error}")

    @host.on_message
    def _(event):
        # Runs while the View waits for an answer, so only record it here.
        question.set(event)

    @reactive.effect
    @reactive.event(question)
    async def _():
        event = question()
        await chat.append_message({"role": "user", "content": event.text})
        ctx = host.model_context(event.widget_id)
        sel = (ctx.structured_content or {}).get("selection") if ctx else None
        if not sel:
            await chat.append_message("Select a region in the explorer first.")
            return
        await chat.append_message(await answer(sel))

    async def answer(sel: dict) -> str:
        """Answer from the shared selection, using a further tool call."""
        # Same product/quarter filters across all regions, to compare against.
        result = await host.call_tool(
            "sales", "query_sales", {"product": sel["product"], "quarter": sel["quarter"]}
        )
        rows = result.structured_content["rows"]
        by_region = {r: sum(x["revenue"] for x in rows if x["region"] == r) for r in REGIONS}
        mine = [x for x in rows if x["region"] == sel["region"]]
        total = by_region[sel["region"]]
        rank = sorted(by_region, key=by_region.get, reverse=True).index(sel["region"]) + 1
        share = 100 * total / max(1, sum(by_region.values()))
        scope = ", ".join(f"{k}: {sel[k]}" for k in ("product", "quarter") if sel[k] != "All") or "all sales"
        lines = [
            f"**{sel['region']}** brought in **${total:,}** ({scope}), "
            f"#{rank} of {len(REGIONS)} regions with {share:.0f}% of the total.",
        ]
        for key, label in (("product", "Biggest product"), ("quarter", "Strongest quarter")):
            if sel[key] == "All":
                sums: dict[str, int] = {}
                for x in mine:
                    sums[x[key]] = sums.get(x[key], 0) + x["revenue"]
                best, best_rev = max(sums.items(), key=lambda kv: kv[1])
                lines.append(f"- {label}: **{best}**, ${best_rev:,} ({100 * best_rev / total:.0f}%).")
        return "\n".join(lines)

    @render.code
    def selection():
        event = latest()
        if event is None:
            return "(nothing yet: click a region)"
        return json.dumps(event.structured_content or {"text": event.content}, indent=2)


app = App(app_ui, server)
