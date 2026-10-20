"""Chat example: an interactive MCP App View inside a ``ui.Chat`` assistant message.

No LLM is required: a tiny keyword "assistant" decides when to call the tool.
Type "time" (or click the suggestion) to get the official "Get Server Time"
MCP App rendered inline in the assistant's reply. Messages the View sends
with ``ui/message`` are posted back into the chat.

    make demo        # starts the demo MCP server and this app on http://127.0.0.1:8000
"""

import os

from shiny import App, ui

from shiny_mcp_host import MCPHost, MCPHostError
from shiny_mcp_host.sandbox import start_sandbox_server

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:3001/mcp")
sandbox = start_sandbox_server(port=int(os.environ.get("SANDBOX_PORT", "8765")))

WELCOME = (
    "Hi! I can show the MCP server's time as an interactive app. "
    "Try <span class='suggestion submit'>What time is it?</span>"
)

app_ui = ui.page_fillable(
    ui.h3("MCP Apps in ui.Chat"),
    ui.chat_ui("chat", greeting=ui.HTML(WELCOME), drawer=False, show_history=False),
    fillable_mobile=True,
)


def server(input, output, session):
    # History persistence is out of scope: widgets live as long as the session.
    chat = ui.Chat(id="chat", history=False)
    host = MCPHost(session, sandbox_url=sandbox.url)
    host.add_server("demo", url=MCP_SERVER_URL)

    @host.on_message
    async def _(event):
        # The View asked the host to add a message to the conversation.
        await chat.append_message({"role": "user", "content": event.text})
        await chat.append_message(f"The app in `{event.tool}` said: _{event.text}_")

    @chat.on_user_submit
    async def _(user_input: str):
        if "time" in user_input.lower():
            # Execute once; render_in_chat only displays the outcome (it never calls the tool).
            result, error = None, None
            try:
                result = await host.call_tool("demo", "get-time", {})
            except MCPHostError as e:
                error = str(e)  # the View still opens, and is told the call failed
            out = await host.render_in_chat(
                chat, "demo", "get-time", {}, result, error=error, text="Here is the server time:", height=420
            )
            if out.tool_failed or out.render_error:
                await chat.append_message(f"Sorry, that didn't work: {out.render_error or out.tool_error}")
        else:
            await chat.append_message("I only know how to tell the time. Ask me *what time is it?*")


app = App(app_ui, server)
