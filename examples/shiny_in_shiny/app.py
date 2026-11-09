"""Shiny in Shiny: a Shiny chat (the host) showing an MCP App that is itself a Shiny app.

1. "Plot iris" -> the "agent" calls explore_dataset; the inner Shiny app opens in the reply.
2. Move the inner app's controls -> it shares the new view as model context (sidebar).
3. "Colour by cylinders" / "show mpg against weight" -> the agent calls set_plot_controls,
   and the controls in the open plot move.
4. "What am I looking at?" or the inner app's button -> answered from the shared context.

No LLM: `plan()` stands in for one. Run everything with `make demo-shiny`.
"""

from __future__ import annotations

import json
import os
import re

from shiny import App, reactive, render, ui

from shiny_mcp_host import MCPHost, MCPHostError
from shiny_mcp_host.host import SANDBOX_URL_ENV, TRUST_APPS_ENV
from shiny_mcp_host.sandbox import start_sandbox_server

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:3001/mcp")
DEV_SANDBOX_URL = (
    None
    if os.environ.get(SANDBOX_URL_ENV) or os.environ.get(TRUST_APPS_ENV)
    else start_sandbox_server(port=int(os.environ.get("SANDBOX_PORT", "8765"))).url
)

COLUMNS = {
    "iris": ["sepal_length", "sepal_width", "petal_length", "petal_width"],
    "cars": ["mpg", "horsepower", "weight", "displacement", "acceleration", "year"],
}
GROUPS = {"iris": ["species"], "cars": ["origin", "cylinders"]}

GREETING = ui.HTML(
    "The plot below is a separate Shiny app, served by an MCP server. Try "
    "<span class='suggestion submit'>Plot iris</span>, then "
    "<span class='suggestion submit'>Show sepal width against sepal length</span>, "
    "<span class='suggestion submit'>Switch to cars, colour by cylinders</span> or "
    "<span class='suggestion submit'>What am I looking at?</span>"
)


def _find_column(text: str, dataset: str) -> list[str]:
    """Columns mentioned in text, in order of appearance ("petal length" or petal_length)."""
    found = []
    for col in COLUMNS[dataset]:
        m = re.search(col.replace("_", "[ _]"), text)
        if m:
            found.append((m.start(), col))
    return [c for _, c in sorted(found)]


def plan(text: str, current: dict | None) -> tuple[str, dict]:
    """Stand-in for an LLM: ("open" | "set" | "describe" | "help", arguments)."""
    lower = text.lower()
    dataset = next((d for d in COLUMNS if d in lower), None)
    if re.search(r"\b(what|describe|explain|tell me)\b", lower) and current:
        return "describe", {}
    target = dataset or (current or {}).get("dataset") or "iris"
    args: dict[str, str] = {}
    if dataset:
        args["dataset"] = dataset
    cols = _find_column(lower, target)
    if len(cols) >= 2:  # "A against B" / "A vs B": y first
        args["y"], args["x"] = cols[0], cols[1]
    elif len(cols) == 1:
        args["y" if re.search(r"\by\b|vertical", lower) else "x"] = cols[0]
    if re.search(r"no colou?r|uncolou?red", lower):
        args["color"] = "none"
    elif g := next((g for g in GROUPS[target] if g in lower), None):
        args["color"] = g
    opening = re.search(r"\b(plot|open|new)\b", lower) and not current or re.search(r"\bnew\b", lower)
    if opening or (current is None and args):
        return "open", args
    if args and current:
        return "set", args
    return "help", {}


app_ui = ui.page_sidebar(
    ui.sidebar(
        ui.h5("What the host received"),
        ui.p("Model context from the inner Shiny app:", class_="text-muted small"),
        ui.output_code("context"),
        width=340,
    ),
    ui.chat_ui("chat", greeting=GREETING, drawer=False, show_history=False),
    title="Shiny in Shiny · an MCP App written in Shiny",
    fillable=True,
)


def server(input, output, session):
    chat = ui.Chat(id="chat", history=False)
    host = MCPHost(session, sandbox_url=DEV_SANDBOX_URL)
    host.add_server("plots", url=MCP_SERVER_URL)
    latest = reactive.value(None)  # newest model context from any plot
    question = reactive.value(None)

    @host.on_model_context
    def _(event):
        if not event.cleared:
            latest.set(event)

    @host.on_message
    def _(event):
        question.set(event)

    def current() -> dict | None:
        with reactive.isolate():
            event = latest()
        return (event.structured_content or None) if event else None

    @chat.on_user_submit
    async def _(text: str):
        ctx = current()
        action, args = plan(text, (ctx or {}).get("controls"))
        try:
            if action == "open":
                result = await host.call_tool("plots", "explore_dataset", args)
                await host.render_in_chat(
                    chat, "plots", "explore_dataset", args, result, text=result.content[0].text, height=470
                )
            elif action == "set":
                result = await host.call_tool(
                    "plots", "set_plot_controls", {"session": ctx["session"], **args}
                )
                changes = ", ".join(f"{k} → {v}" for k, v in args.items())
                await chat.append_message(
                    f"Called `set_plot_controls` ({changes})."
                    if not result.is_error
                    else f"Couldn't change the plot: {result.content[0].text}"
                )
            elif action == "describe":
                await chat.append_message(describe(ctx))
            else:
                await chat.append_message(
                    "Try *plot iris*, *show mpg against weight*, *colour by origin* or *what am I looking at?*"
                )
        except MCPHostError as e:
            await chat.append_message(f"Sorry, the tool failed: {e}")

    @reactive.effect
    @reactive.event(question)
    async def _():
        event = question()
        await chat.append_message({"role": "user", "content": event.text})
        await chat.append_message(describe(current()))

    def describe(ctx: dict | None) -> str:
        if not ctx:
            return "Nothing is open yet."
        c, s = ctx["controls"], ctx["stats"]
        r = s["correlation"]
        strength = "strong" if abs(r) > 0.7 else "moderate" if abs(r) > 0.4 else "weak"
        lines = [
            f"You're looking at **{c['dataset']}**: `{c['y']}` against `{c['x']}` "
            f"({s['n']} points). The correlation is **{r}**, a {strength} "
            f"{'positive' if r > 0 else 'negative'} relationship."
        ]
        for g, v in (s.get("groups") or {}).items():
            lines.append(
                f"- **{g}** ({v['n']}): mean {c['x']} {v['mean_' + c['x']]}, mean {c['y']} {v['mean_' + c['y']]}"
            )
        return "\n".join(lines)

    @render.code
    def context():
        event = latest()
        if event is None:
            return "(nothing yet: open a plot)"
        return json.dumps(event.structured_content, indent=2)


app = App(app_ui, server)
