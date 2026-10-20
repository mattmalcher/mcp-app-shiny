"""Standalone example: render the official "Get Server Time" MCP App in a Shiny page.

Start the demo MCP server and this app (see examples/README.md), then open
http://127.0.0.1:8000 :

    make demo-standalone
"""

import os

from shiny import App, reactive, render, ui

from shiny_mcp_host import MCPHost, mcp_widget
from shiny_mcp_host.sandbox import start_sandbox_server

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:3001/mcp")
# The sandbox proxy must live on a different origin than the Shiny app.
sandbox = start_sandbox_server(port=int(os.environ.get("SANDBOX_PORT", "8765")))

app_ui = ui.page_fluid(
    ui.h2("MCP Apps in Python Shiny"),
    ui.p("The widget below is the unmodified official ext-apps example View, running in a sandbox."),
    ui.input_action_button("run", "Call get-time again"),
    mcp_widget("demo_widget", height=420),
    ui.layout_columns(
        ui.card(ui.card_header("Messages sent by the View"), ui.output_code("messages")),
        ui.card(ui.card_header("Model context from the View"), ui.output_code("context")),
    ),
)


def server(input, output, session):
    host = MCPHost(session, sandbox_url=sandbox.url)
    host.add_server("demo", url=MCP_SERVER_URL)
    sent = reactive.value[list[str]]([])

    @host.on_message
    def _(event):
        sent.set([*sent.get(), f"[{event.widget_id}] {event.role}: {event.text}"])

    @reactive.effect
    @reactive.event(input.run, ignore_none=False)
    async def _():
        await host.call_and_render("demo", "get-time", {}, target="demo_widget")

    @render.code
    def messages():
        return "\n".join(sent()) or "(none yet)"

    @render.code
    def context():
        ctx = host.model_context("demo_widget")
        if ctx is None:
            return "(none)"
        return repr(ctx.structured_content or ctx.content)


app = App(app_ui, server)
