# Examples

Both examples use the **unmodified official** ext-apps example server
`@modelcontextprotocol/server-basic-vanillajs` (the "Get Server Time" MCP App),
installed from npm by `make setup`.

| Example | Command | What it shows |
|---|---|---|
| [`chat/app.py`](chat/app.py) | `make demo` | `ui.Chat`; type "what time is it?" and the View appears inside the assistant reply. "Send Message" in the View posts back into the chat. |
| [`standalone/app.py`](standalone/app.py) | `make demo-standalone` | A widget in `ui.page_fluid`, plus outputs showing `ui/message` and model-context events. |

Then open **http://127.0.0.1:8000** (use `127.0.0.1`, not `localhost`: the
sandbox runs at `http://localhost:8765`, a different origin and hostname).

Manual equivalent:

```bash
PORT=3001 node frontend/node_modules/@modelcontextprotocol/server-basic-vanillajs/dist/index.js &
MCP_SERVER_URL=http://127.0.0.1:3001/mcp shiny run --host 127.0.0.1 --port 8000 examples/chat/app.py
```

Environment variables: `MCP_SERVER_URL` (default `http://127.0.0.1:3001/mcp`),
`SANDBOX_PORT` (default `8765`).

The test-only fixture server (`tests/fixtures/fixture_server.py`) is a Python
MCP Apps server with a counter View, a malicious View and broken resources;
`tests/e2e/apps/fixture_app.py` hosts it.
