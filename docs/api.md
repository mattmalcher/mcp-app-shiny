# API reference

Everything is importable from `shiny_mcp_host` unless noted. MCP server
authors only need [`shiny_mcp_host.view.app_html`](#app_html), which does not
import Shiny.

## Executing vs rendering

One rule: **methods named `render_*` never call a tool.** They display an
outcome you already have, so side effects never run twice.

| You have… | Call |
|---|---|
| nothing yet, and want the data | `await host.call_tool(server, tool, args)` |
| a result (e.g. from your agent loop) | `await host.render_result(server, tool, args, result, target=…)` or `await host.render_in_chat(chat, server, tool, args, result)` |
| a failed call (an exception message) | the same, with `error=str(e)` instead of `result` |
| nothing, and want the tool run *and* shown | `await host.call_and_render(server, tool, args, target=…)` |

`render_result` and `render_in_chat` require exactly one of `result` or
`error` and raise `ValueError` otherwise. With `error`, a tool that has a UI
still opens its View, which receives `ui/notifications/tool-cancelled` with
that reason; a tool without a UI shows the error in the widget.

## `MCPHost`

`MCPHost(session=None, *, id="mcp", sandbox_url=None, policy=None, theme="light", dangerously_trust_apps=False)`:
one per Shiny session. `sandbox_url` falls back to `$SHINY_MCP_SANDBOX_URL`;
without either, or when it is the app's own origin, it raises
`SandboxConfigError` (the host never falls back to an unsafe setup).
`dangerously_trust_apps=True` is the explicit exception: no sandbox at all,
for apps you control ([trusted mode](architecture.md#trusted-mode-dangerously_trust_appstrue)).

| Method | |
|---|---|
| `add_server(name, *, url=None, transport="streamable_http", command=None, args=(), env=None, headers=None, auth=None)` | Register a server (connects lazily). `transport="stdio"` launches `command`. `headers` / `auth` (an `httpx2.Auth`) stay in Python. |
| `await list_tools(server, *, refresh=False)` | `ToolInfo`s with `has_ui`, `ui_resource_uri`, `visibility` |
| `await call_tool(server, tool, arguments=None)` | Execute only. Returns a `CallToolResult` (tool-level failures have `is_error`); raises `MCPHostError` (`ServerUnavailableError`, `RequestTimeoutError`, `ToolCallError`) when the call itself fails. |
| `await render_result(server, tool, arguments, result=None, *, target, error=None)` | Render an existing outcome into `target` |
| `await render_in_chat(chat, server, tool, arguments=None, result=None, *, error=None, text=None, height=360)` | Append an assistant message with a new widget (and optional `text` above it) to a `ui.Chat`, then render an existing outcome into it |
| `await call_and_render(server, tool, arguments=None, *, target)` | Execute and render; the UI resource is fetched while the tool runs. A failed call is reported, not raised. |
| `widget(id=None, *, height=360)` | A widget tag bound to this host (auto id when omitted); usable as `target` |
| `on_model_context(fn)` | Decorator: `fn(ModelContextEvent)` whenever a View shares context, clears it, or its widget is removed (a cleared event) |
| `on_message(fn)` | Decorator: `fn(AppMessageEvent)` when a View asks to add a message to the conversation. Return `False` to reject. With no callback, messages are rejected. |
| `model_context(target=None)` | Reactive: the latest `ModelContextEvent` of one widget (or `None`), or a dict of all |
| `widget_status(target)` | Reactive: the widget's lifecycle status (below), or `None` |
| `await remove_widget(target)` | Tear down a widget and forget it (below) |
| `await close()` | Cancel pending requests, forget all widget state and close MCP transports; runs automatically when the session ends |

`target` is a widget id (namespaced in modules) or a widget tag.
`mcp_widget(id, *, host="mcp", height=360)` is the mount point for static or
dynamic UI; widgets may be mounted before or after rendering into them.

## `RenderResult`

Returned by the three render methods. It answers three separate questions:

| Field | Meaning |
|---|---|
| `tool_failed` | The tool failed: the call raised, `error=` was given, or the result has `isError`. |
| `tool_error` | Why the call failed when there is no result; `None` otherwise. |
| `result` | The `CallToolResult` rendered (or `None`). |
| `display` | What Python sent to the widget: `"view"` (the MCP App), `"text"` (the tool has no UI), `"error"` (an error message), or `None`. |
| `superseded` | `display is None`: a newer render to the same widget (or `remove_widget`) replaced this one. Nothing from it is shown. |
| `render_error` | Why the host could not present the outcome: unknown server or tool, invalid UI resource, malformed result. |
| `widget_id`, `server`, `tool`, `resource_uri` | Identification. |

`display == "view"` means the View was *submitted*, not that it works: the
browser loads the sandbox and starts the View afterwards. Watch
`widget_status(target)`; render errors are shown in the widget and never
raised.

## Widget lifecycle

`widget_status(target)` is one of:

| Status | Meaning |
|---|---|
| `"waiting"` | Mounted, nothing rendered yet |
| `"loading"` | A render is in progress in Python |
| `"rendered"` | The outcome was sent to the browser: a View that has not initialized yet, or a text fallback |
| `"initialized"` | The View completed the MCP Apps handshake in the browser. The only status that confirms it is running. |
| `"error"` | Python could not render, or the browser could not load the sandbox or start the View (see `data-error-code`) |
| `None` | Unknown or removed widget |

Rules the host enforces:

- **Newest render wins.** Each render to a widget is numbered; an older call
  that finishes later is dropped (`superseded`), including its errors.
- **Stale requests are refused.** A View may only talk to Python while it is
  the widget's current render in the current mount. Re-rendering, unmounting
  (e.g. the chat message is removed) and `remove_widget` cancel its in-flight
  requests and settle them in the browser.
- **`remove_widget(target)`** removes the widget's status (`None`), its model
  context (`model_context(target)` is `None`, and callbacks receive a cleared
  event), its render and in-flight requests, and tears the View down. Rendering
  to the same id later starts afresh.
- **Session end** runs `close()`: every pending request is cancelled, widget
  state is forgotten, Views stop receiving responses, and MCP transports
  close.

## Events

`ModelContextEvent(widget_id, server, tool, content, structured_content)`,
with `cleared` when both are empty. `AppMessageEvent(widget_id, server, tool,
role, content)`, with `text` joining its text blocks. Callbacks may be sync or
async. Those triggered by a View run inside the session's reactive context
(the cleared event from `remove_widget` runs wherever you called it from);
`on_message` callbacks run
while the View waits for the answer, so record the event and do slow work in
a reactive effect (see `examples/sales_explorer/app.py`).

## `HostPolicy`

What Views may do. Defaults are restrictive.

| Option | Default | |
|---|---|---|
| `app_tools` | `None` | Explicit allowlist `{server: [tool, …]}`. When unset, a View may call its own tool and tools its server marks `visibility: ["app"]`, on its own server only. |
| `allowed_csp_domains` | none | External domains a View's declared CSP may use; others are dropped |
| `trust_declared_csp` | `False` | Accept every domain a View declares (not recommended) |
| `allowed_permissions` | none | Iframe permissions a View may be granted when it asks: `camera`, `microphone`, `geolocation`, `clipboardWrite` |
| `allow_open_links` | `True` | `ui/open-link` for http(s) URLs |
| `allow_resource_read` | `True` | `resources/read` on the View's own server |
| `view_same_origin` | `False` | Give Views a real origin (storage); requires a different sandbox *hostname* |
| `max_resource_bytes`, `max_tool_payload_bytes`, `max_pending_per_widget`, `request_timeout` | | Limits |

## Errors

`MCPHostError` (base, with a `code`), `SandboxConfigError`,
`ServerUnavailableError`, `RequestTimeoutError`, `ToolCallError`,
`ResourceError`, `PolicyError`.

## <a id="app_html"></a>`shiny_mcp_host.view.app_html`

`app_html(*, body, script, title="MCP App", head="")` returns a complete
HTML page for a `ui://` resource with the official MCP Apps JavaScript SDK
inlined as the global `McpApps`. `script` runs as a module after the SDK;
`head` takes extra head content such as a `<style>` block. No Node or build step; the View itself is
still HTML, CSS and JavaScript. See the README and
`examples/sales_explorer/server.py`.

## Sandbox (`shiny_mcp_host.sandbox`)

| | |
|---|---|
| `sandbox_app(allowed_host_origins=…)` | ASGI app serving the sandbox proxy; deploy on its own hostname |
| `start_sandbox_server(port=8765, *, public_host="localhost", …)` | Development only: runs it in a background thread |
| `check_sandbox(sandbox_url, host_origin)` / `shiny-mcp-sandbox --check URL --host-origin ORIGIN` | Server-side diagnosis of a deployed sandbox; see [deployment.md](deployment.md#diagnosing-sandbox-problems) |
| `frame_ancestors_verdict(policies, host_origin, self_origin)` | Whether CSP headers let an origin frame a page: `allows`, `blocks`, `inconclusive` or `absent` (CSP Level 3 source matching) |
| `shiny-mcp-sandbox [--host H] [--port P] [--allow-host-origin O …]` | Serve it from the command line |

## Debugging

- Browser console: `[shiny-mcp-host]` (adapter) and `[shiny-mcp sandbox]`.
- On the `<shiny-mcp-widget>` element: `data-state` (`mounting`, `loading`,
  `ready`, `error`, `fallback`, `unmounted`, `disconnected`, `removed`) and, on errors,
  `data-error-code`: `sandbox_config` (unsafe sandbox URL, refused before
  loading), `sandbox_unreachable` (the browser could not load or embed the
  sandbox), `view_init_timeout` (the View loaded but never initialized),
  `view_failed` (other browser-side failures), or a Python code such as
  `server_unavailable`, `invalid_resource`, `tool_failed`, `timeout`.
- Python logs to the `shiny_mcp_host` logger, including browser-side View
  errors with their code.
