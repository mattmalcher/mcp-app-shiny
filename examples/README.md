# Examples

| Example | Command | What it shows |
|---|---|---|
| [`sales_explorer/`](sales_explorer/) | `make demo` | **The showcase.** An interactive sales explorer in `ui.Chat`. The agent's existing tool result is rendered with `render_result`; filters call tools through Python; the selected region comes back as model context; the app's question is answered by Python. |
| [`chat/app.py`](chat/app.py) | `make demo-clock` | Smoke test: the unmodified official ext-apps "Get Server Time" app (npm `@modelcontextprotocol/server-basic-vanillajs`) in `ui.Chat`. |
| [`standalone/app.py`](standalone/app.py) | `make demo-standalone` | The same official app in a plain `ui.page_fluid`, with outputs for `ui/message` and model context. |

Each command starts the MCP server, the sandbox and the Shiny app, checks ports
and prerequisites first, and prints the URL to open
(**http://127.0.0.1:8000**). Ctrl+C stops everything. Options:
`python scripts/demo.py --help` (ports via `--mcp-port`, `--app-port`,
`--sandbox-port`).

`sales_explorer/` has two parts: `server.py` is an MCP server with the
`explore_sales` tool and its View (`view.html` plus the bundle built from
`frontend/examples/sales-view.ts`); `app.py` is the Shiny app. The data is
synthetic.
