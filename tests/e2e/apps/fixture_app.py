"""Shiny app used by the browser tests, driven by the fixture MCP server.

Tests send commands with ``Shiny.setInputValue("cmd", {...}, {priority: "event"})``.
Sandbox URL comes from SHINY_MCP_SANDBOX_URL (started by the test harness);
FIXTURE_TRUSTED=1 runs the host with dangerously_trust_apps=True instead.
"""

import json
import os

from shiny import App, reactive, render, ui

from shiny_mcp_host import MCPHost, mcp_widget

MCP_SERVER_URL = os.environ["MCP_SERVER_URL"]
TRUSTED = os.environ.get("FIXTURE_TRUSTED") == "1"

app_ui = ui.page_fluid(
    ui.h2("Fixture host"),
    ui.div(
        mcp_widget("w_a", height=300),
        mcp_widget("w_b", height=300),
        mcp_widget("w_evil", height=400),
        mcp_widget("w_err", height=200),
        id="widgets",
    ),
    ui.output_code("contexts"),
    ui.output_code("messages"),
    ui.output_code("pwned"),
    ui.output_code("render_results"),
)


def server(input, output, session):
    host = MCPHost(session, dangerously_trust_apps=TRUSTED)
    host.add_server("fx", url=MCP_SERVER_URL)
    sent = reactive.value([])
    results = reactive.value({})

    @host.on_message
    def _(event):
        sent.set([*sent.get(), f"{event.widget_id}:{event.text}"])

    @reactive.effect
    @reactive.event(input.cmd)
    async def _():
        cmd = dict(input.cmd())
        action = cmd.get("action", "render")
        if action == "render":
            r = await host.call_and_render(
                "fx", cmd["tool"], dict(cmd.get("args") or {}), target=cmd["target"]
            )
            results.set({**results.get(), cmd["target"]: r.render_error or r.tool_error or "ok"})
        elif action == "remove":
            ui.remove_ui(selector=f"shiny-mcp-widget[data-widget-id='{cmd['target']}']")
        elif action == "insert":
            ui.insert_ui(mcp_widget(cmd["target"], height=300), selector="#widgets", where="beforeEnd")

    @render.code
    def contexts():
        return json.dumps(
            {k: v.structured_content or v.content for k, v in sorted(host.model_context().items())},
            sort_keys=True,
        )

    @render.code
    def messages():
        return "\n".join(sent())

    @render.code
    def pwned():
        return f"pwned={input.pwned() if 'pwned' in input else None}"

    @render.code
    def render_results():
        return json.dumps(results(), sort_keys=True)


app = App(app_ui, server)
