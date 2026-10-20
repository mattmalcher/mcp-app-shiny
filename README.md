# shiny-mcp-host

Render standards-compliant [MCP Apps](https://github.com/modelcontextprotocol/ext-apps)
(interactive UIs attached to MCP tools) inside **Python Shiny** apps — in a
normal page or inside a `ui.Chat` assistant message — without R and without
writing JavaScript.

- Python owns every MCP connection (Streamable HTTP or stdio); tokens and
  server URLs never reach the browser.
- Each View runs in the official `AppBridge` lifecycle, inside a double-iframe
  sandbox on a **distinct origin**, with spec-default CSP.
- Tool calls from a View go browser → Shiny → Python → MCP, and are authorised
  in Python.
- Model-context updates and `ui/message` requests from Views reach Python
  callbacks, per widget.

Implements MCP Apps spec `2026-01-26` with `@modelcontextprotocol/ext-apps` 2.0.3.
See [docs/protocol-contract.md](docs/protocol-contract.md) for exactly what is
supported.

## Architecture

```
Shiny app (Python)                                  Browser
┌───────────────────────────────┐                  ┌──────────────────────────────┐
│ MCPHost (one per session)     │  makeRequest     │ Shiny page / ui.Chat         │
│  ServerConnection per server  │◄────────────────►│ <shiny-mcp-widget> elements  │
│  HostPolicy enforcement       │  custom messages │ AppBridge per View           │
└──────────────┬────────────────┘                  └──────────────┬───────────────┘
               │ MCP (Streamable HTTP / stdio)                    │ postMessage (origin +
               ▼                                                  ▼ source + nonce checked)
        MCP server: tools + ui:// resources        sandbox proxy iframe (other origin)
                                                     └─ View iframe (opaque origin, CSP)
```

## Install

```bash
pip install shiny-mcp-host          # once published; or: pip install dist/*.whl
```

The wheel ships the compiled browser bundle; Node is only needed to build from
source.

## Quickstart

```python
from shiny import App, reactive, ui
from shiny_mcp_host import MCPHost, mcp_widget
from shiny_mcp_host.sandbox import start_sandbox_server

sandbox = start_sandbox_server(port=8765)   # distinct origin: http://localhost:8765

app_ui = ui.page_fluid(
    ui.h2("MCP Apps in Python Shiny"),
    mcp_widget("demo_widget", height=420),
)

def server(input, output, session):
    host = MCPHost(session, sandbox_url=sandbox.url)
    host.add_server("demo", url="http://127.0.0.1:3001/mcp")

    @reactive.effect
    async def _():
        await host.call_and_render("demo", "get-time", {}, target="demo_widget")

app = App(app_ui, server)
```

Run it with `shiny run --host 127.0.0.1 app.py` and open
`http://127.0.0.1:8000` (see [Sandbox origin setup](#sandbox-origin-setup)).

### In `ui.Chat`

```python
chat = ui.Chat(id="chat", history=False)
host = MCPHost(session, sandbox_url=sandbox.url)
host.add_server("demo", url="http://127.0.0.1:3001/mcp")

@chat.on_user_submit
async def _(user_input: str):
    # Appends an assistant message containing a widget, then renders into it.
    await host.render_in_chat(chat, "demo", "get-time", {}, text="Here is the server time:")
```

Full, runnable versions: [examples/standalone/app.py](examples/standalone/app.py)
and [examples/chat/app.py](examples/chat/app.py).

## API

| | |
|---|---|
| `mcp_widget(id, *, host="mcp", height=360)` | UI mount point (`<shiny-mcp-widget>`); works in static UI, dynamic UI and chat messages |
| `MCPHost(session=None, *, id="mcp", sandbox_url=None, policy=None, theme="light")` | one per session; `sandbox_url` falls back to `$SHINY_MCP_SANDBOX_URL` |
| `host.add_server(name, *, url=None, transport="streamable_http", command=None, args=(), env=None, headers=None, auth=None)` | register a server (lazy connect); `transport="stdio"` launches a subprocess |
| `await host.list_tools(server)` | `ToolInfo` list with `ui_resource_uri`, `visibility`, `has_ui` |
| `await host.call_tool(server, tool, arguments)` | plain call, no rendering (for your own LLM loop) |
| `await host.call_and_render(server, tool, arguments, *, target)` | call + render into a widget; returns `RenderResult` (errors are shown in the widget and returned, not raised) |
| `host.widget(id=None, *, height=360)` | a widget tag bound to this host (auto id) |
| `await host.render_in_chat(chat, server, tool, arguments, *, text=None)` | append an assistant message with a widget and render into it |
| `host.on_model_context(fn)` / `host.model_context(target=None)` | callback / reactive read of `ui/update-model-context`, per widget |
| `host.on_message(fn)` | callback for `ui/message`; return `False` to reject |
| `host.widget_status(target)` | reactive lifecycle status |
| `await host.remove_widget(target)`, `await host.close()` | teardown (close also runs automatically when the session ends) |

## Sandbox origin setup

The MCP Apps spec requires the sandbox proxy to be on a different origin than
the host page; a different URL path on the same host and port is **not** an
origin boundary. `MCPHost` refuses to run without one — there is no unsafe
fallback.

- **Development:** `start_sandbox_server(port=8765)` runs the proxy in a
  background thread at `http://localhost:8765`. Open your app at
  `http://127.0.0.1:<port>` so the two also differ in hostname (cookies are
  shared across ports of the same hostname). Or run it separately:
  `shiny-mcp-sandbox --port 8765` (alias for `python -m shiny_mcp_host.sandbox`).
- **Production:** serve `shiny_mcp_host.sandbox.sandbox_app(allowed_host_origins=["https://app.example.com"])`
  with any ASGI server on a dedicated domain over HTTPS, and pass
  `sandbox_url="https://sandbox.example-usercontent.com/sandbox.html"`.
  The server only embeds into the listed host origins (`frame-ancestors`).

## Permission model

`HostPolicy` is enforced in Python:

- **Tools from Views:** same server only; the tool must exist and its
  `_meta.ui.visibility` must include `"app"` (spec). On top of that, by default
  a View may call only the tool that produced it and tools explicitly marked
  `visibility: ["app", …]`. Pass `app_tools={"server": {"tool", …}}` for an
  explicit allowlist. Rejected calls never reach the MCP server.
- **CSP:** the sandbox uses the spec's restrictive default. Domains a resource
  declares in `_meta.ui.csp` are dropped unless listed in
  `allowed_csp_domains` (or `trust_declared_csp=True`); malformed entries and
  non-http(s)/ws(s) schemes are always rejected.
- **Permissions** (camera, microphone, geolocation, clipboardWrite): none by
  default; grant with `allowed_permissions`.
- **Links:** `ui/open-link` accepts only absolute http(s) URLs (`allow_open_links`).
- **Limits:** `max_resource_bytes` (5 MB), `max_tool_payload_bytes` (256 KB),
  `max_pending_per_widget` (16), `request_timeout` (30 s).
- `view_same_origin=False` (default) runs the View in an opaque origin.

## Transports

Streamable HTTP (`url=...`) and stdio (`transport="stdio", command=..., args=[...]`).
For authenticated remote servers pass `headers={"Authorization": ...}` or an
`httpx2.Auth` via `auth=`; full OAuth flows are not implemented.

## Limitations

- No LLM/agent loop — you decide which tool to call (or wire your own LLM to
  `call_tool` / `render_in_chat`).
- Inline display mode only; no fullscreen, partial tool-input streaming,
  file download, sampling, or theme changes after mount.
- Conversation history / bookmark restoration of widgets is not supported
  (use `ui.Chat(history=False)`); widgets live as long as the Shiny session.
- Tested on Chromium only (Firefox is supported by the harness via
  `E2E_BROWSER=firefox` but was not available in the development environment).
- Not yet published to PyPI.

## Debugging

- Browser console: the adapter logs with a `[shiny-mcp-host]` prefix; the
  sandbox with `[shiny-mcp sandbox]`.
- Widget state is exposed as attributes on the element:
  `data-state` (`mounting`, `loading`, `ready`, `error`, `fallback`,
  `disconnected`), `data-error-code`, `data-render-id`, `data-mount-id`.
- Python logs to the `shiny_mcp_host` logger (rendered resource, applied CSP,
  rejected requests, View logs).

## Development

Requires `uv`, Node 20+ and npm.

```bash
make setup             # uv sync --frozen, npm ci, build browser assets
make build             # browser bundle + wheel/sdist into dist/
make test-unit         # pytest unit + Vitest
make test-integration  # fixture + official demo MCP servers, Python host
make test-e2e          # servers + Shiny apps + Playwright (Chromium)
make test-security     # every test marked `security`, all layers
make test-compat       # clones + builds upstream basic-host, same demo there (A15)
make test-dist         # installs the wheel into a clean venv and checks assets
make test              # all of the above + lint
make demo              # official demo server + chat example on http://127.0.0.1:8000
```

Layout: `src/shiny_mcp_host/` (Python package; `static/` holds the compiled
bundle), `frontend/` (TypeScript sources, Vitest), `examples/`, `tests/`
(`unit`, `integration`, `e2e`, `fixtures`), `docs/protocol-contract.md`.
