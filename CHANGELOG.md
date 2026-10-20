# Changelog

## Unreleased

### Breaking changes

- **`render_in_chat()` no longer calls the tool.** Methods named `render_*`
  only display an outcome you already have. Pass the result of your own
  `call_tool()`, or `error=` for a call that failed:

  ```python
  # before: executed the tool when result= was omitted
  await host.render_in_chat(chat, "sales", "explore_sales", args)

  # after: execute, then render (the tool runs exactly once)
  result = await host.call_tool("sales", "explore_sales", args)
  await host.render_in_chat(chat, "sales", "explore_sales", args, result)
  ```

  To execute and render in one step, append `host.widget()` yourself and use
  `call_and_render(..., target=tag)`. `result` may now be passed positionally.
- **`render_result()` and `render_in_chat()` take exactly one of `result` or
  `error`** and raise `ValueError` otherwise, before anything is appended or
  rendered. Previously `render_result()` with neither showed "No tool result
  was provided" in the widget, and with both silently rendered the result.
- **`RenderResult.ok` and `RenderResult.error` are removed.** `ok` read as
  "the app works" but only meant "no error string". They are replaced by
  fields that answer one question each:

  | Question | Read |
  |---|---|
  | Did the tool fail? | `tool_failed` (the call raised, `error=` was given, or `result.isError`); `tool_error` holds the reason when there is no result |
  | What did Python show? | `display`: `"view"`, `"text"`, `"error"`, or `None` when `superseded` by a newer render |
  | Why couldn't it be shown? | `render_error` (unknown server/tool, invalid UI resource, malformed result) |
  | Is the View running in the browser? | `host.widget_status(target) == "initialized"` (reactive; not known when the render call returns) |

  Migration: `if not out.ok:` → `if out.render_error:` (could not show it) or
  `if out.tool_failed:` (the tool failed); `out.error == "superseded"` →
  `out.superseded`.

### Fixed

- `remove_widget()` now also clears the widget's model context:
  `model_context(target)` returns `None` and model-context callbacks receive a
  cleared event. Its status is removed under the reactive lock, its in-flight
  View requests are cancelled and settled, a render still in progress for it is
  dropped (`superseded`), and later requests from its View are rejected.

### Added

- **Experimental trusted mode**: `MCPHost(session, dangerously_trust_apps=True)`
  renders Views without a sandbox server or second hostname, for apps you
  control. Each View runs in one opaque-origin `srcdoc` iframe with its CSP in
  a `<meta>` tag; `HostPolicy` still authorises every request. See
  [docs/architecture.md](docs/architecture.md#trusted-mode-dangerously_trust_appstrue).

- `shiny-mcp-sandbox --check SANDBOX_URL --host-origin APP_ORIGIN` and
  `shiny_mcp_host.sandbox.check_sandbox()`: server-side diagnosis of a
  deployed sandbox (reachability, redirects, TLS, authentication,
  `frame-ancestors` / `X-Frame-Options`).
  It matches `frame-ancestors` source expressions properly (CSP Level 3:
  wildcards, ports, scheme upgrade; every policy must allow the app) and
  reports forms it does not model as `INCONCLUSIVE`, never as a pass.
- Browser-side failures carry distinct `data-error-code`s:
  `sandbox_config`, `sandbox_unreachable` (with next steps in the message),
  `view_init_timeout`, `view_failed`; the code is also logged by Python.
- Posit Connect deployment recipe: `examples/posit_connect/`,
  `make connect-bundles`, `make rehearse-connect`, `scripts/check_deployment.py`,
  [docs/deployment.md](docs/deployment.md) and the validation runbook
  [docs/posit-connect-validation.md](docs/posit-connect-validation.md).
- `scripts/check_deployment.py` always writes `evidence.json` (crashes become
  failed checks with tracebacks; an interrupted run is never `passed`), and
  only counts an explicit 401/403 or login redirect as proof that the MCP
  server requires authentication (timeouts and other errors are
  `INCONCLUSIVE`).
- The sales explorer example reads `SHINY_MCP_SANDBOX_URL` (deployed sandbox)
  and `MCP_SERVER_API_KEY` (sent as `Authorization: Key …`, Python-side only).

- `src/shiny_mcp_host/static/README.md` says where each shipped browser file
  comes from (generated from `frontend/`, or vendored from npm) and how to
  regenerate it. `frontend/build.mjs` now also writes
  `static/THIRD_PARTY_LICENSES.md` with the license text of every npm package
  bundled into `shiny-mcp-host.js` and `sandbox.js`; previously the bundles
  kept only one license comment. The wheel check requires the file.

### Internal

- Per-widget state (records, render ordering, reactive status and model
  context) moved from `MCPHost` into `WidgetStore` (`_widgets.py`), so it is
  created and removed in one place. `MCPHost` remains the public coordinator.
