# Protocol contract

What `shiny-mcp-host` implements, against which upstream versions, and how the
pieces talk to each other.

## Versions this was built and tested against

| Component | Version |
|---|---|
| MCP Apps specification | stable `2026-01-26` (`specification/2026-01-26/apps.mdx`) |
| `@modelcontextprotocol/ext-apps` (AppBridge, App SDK) | `2.0.3` |
| ext-apps repository (basic-host baseline, A15) | commit `82221c0c8ce7661efa6771c9d461511b1650495f` |
| Official demo server `@modelcontextprotocol/server-basic-vanillajs` | `2.0.3` (npm, unmodified) |
| Python MCP SDK `mcp` | `2.3.0` |
| `shiny` / `shinychat` | `1.8.0` / `0.7.2` |
| Node / Python | Node 22.22; Python 3.12 (CI tests), 3.13 (also tested), 3.11 (clean wheel install and Connect bundle rehearsal) |
| Playwright (Python) / Chromium | `1.56.0` / 141 |

Exact Python versions are pinned in `uv.lock`, JavaScript versions in
`frontend/package-lock.json`. Where the browser files in the package come from
is described in `src/shiny_mcp_host/static/README.md`.

## Upstream SDK symbols used

Browser (`@modelcontextprotocol/ext-apps/app-bridge`):

- `AppBridge` constructed with `client = null` — Python owns the MCP
  connection, so `oncalltool` / `onreadresource` are set manually and relay to
  Python.
- `AppBridge` handlers: `oncalltool`, `onreadresource`, `onupdatemodelcontext`,
  `onmessage`, `onopenlink`, `onrequestdisplaymode`; events
  `initialized`, `sizechange`, `loggingmessage`, `requestteardown`.
- Host → View: `sendSandboxResourceReady`, `sendToolInput`, `sendToolResult`,
  `sendToolCancelled`, `teardownResource`, `close`.
- `buildAllowAttribute` (permission policy), types `McpUiHostCapabilities`,
  `McpUiHostContext`, `McpUiResourcePermissions`.
- `JSONRPCMessageSchema` from `@modelcontextprotocol/core` (message
  validation in our origin-checking transport).

Python: `mcp.Client` (Streamable HTTP or stdio), `mcp.client.advertise` to
advertise the `io.modelcontextprotocol/ui` extension with
`mimeTypes: ["text/html;profile=mcp-app"]`. The Python SDK has no client-side
Apps helpers, so `get_tool_ui_resource_uri` is a faithful port of the official
`getToolUiResourceUri` (nested `_meta.ui.resourceUri`, falling back to the
deprecated flat `_meta["ui/resourceUri"]`; non-`ui://` values are errors).

## Host capabilities advertised to Views

```json
{
  "serverTools": {},
  "serverResources": {},          // only if HostPolicy.allow_resource_read
  "openLinks": {},                // only if HostPolicy.allow_open_links
  "logging": {},
  "updateModelContext": {"text": {}, "structuredContent": {}},
  "message": {"text": {}},
  "sandbox": {"csp": <applied csp>, "permissions": <granted permissions>}
}
```

Host context: `theme`, `platform: "web"`, `displayMode: "inline"`,
`availableDisplayModes: ["inline"]`, `containerDimensions`, `locale`,
`timeZone`, `toolInfo.tool`.

## Supported messages

| Direction | Message | Support |
|---|---|---|
| View → Host | `ui/initialize`, `ui/notifications/initialized`, `ping` | handled by `AppBridge` |
| View → Host | `tools/call` | relayed to Python; authorised by `HostPolicy`; same server only |
| View → Host | `resources/read` | relayed to Python (same server, size-bounded) |
| View → Host | `ui/update-model-context` | Python callback + per-widget reactive store |
| View → Host | `ui/message` | Python callback (`MCPHost.on_message`); rejected (`isError`) when none |
| View → Host | `ui/open-link` | http(s) only, validated in JS and Python, opened `noopener,noreferrer` |
| View → Host | `notifications/message` | logged by Python |
| View → Host | `ui/notifications/size-changed` | iframe height (clamped) |
| View → Host | `ui/request-display-mode` | always answers `inline` |
| View → Host | `ui/notifications/request-teardown` | logged, ignored (host controls lifecycle) |
| Host → View | `ui/notifications/tool-input`, `tool-result` / `tool-cancelled` | once each per mount, after `initialized` |
| Host → View | `ui/resource-teardown` | before unmount (1 s timeout) |

**Unsupported** (not advertised): fullscreen/pip display modes, partial tool
input streaming (`tool-input-partial`), `ui/download-file`, sampling,
`resources/list` / templates / prompts from Views, host context change
notifications (theme switching after mount), bookmark/history restoration.

### Initial data lifecycle

`render_result` and `render_in_chat` render an outcome obtained elsewhere (a
result, or the error of a failed call) and never send `tools/call`;
`call_and_render` calls the tool while it fetches the resource.
Either way Python validates the resource, then sends a single `render`
message. Each widget's renders are numbered, and every outcome (View, text
fallback, error) is published through one check, so an older call that
finishes late never replaces a newer render. In the browser the order is:
load sandbox proxy → wait for nonce-bound `sandbox-proxy-ready` →
`AppBridge.connect` → `sendSandboxResourceReady` → wait for
`ui/notifications/initialized` → `sendToolInput` → `sendToolResult` (or
`sendToolCancelled` if the tool failed/timed out). This matches the upstream
basic-host order and the spec rule that nothing is sent before `initialized`.

### Resource validation (Python)

Exactly one content item; its `uri` must equal the requested URI (no
cross-contamination); MIME type must be `text/html;profile=mcp-app`; `text`
or valid base64 `blob`; non-empty; at most `HostPolicy.max_resource_bytes`.
`_meta.ui.csp` / `permissions` are read from the content and filtered by policy;
unknown optional metadata is ignored.

## Shiny ↔ browser envelope (version 1)

Browser → Python, via
`Shiny.shinyapp.makeRequest("shiny-mcp-host:<host_id>", [envelope])`
(handler registered with `session.set_message_handler`; one per `MCPHost`):

```json
{"v": 1, "kind": "tools/call", "widget_id": "w1", "mount_id": "9f3a…",
 "request_id": "9f3a….3", "render_id": 2, "payload": {"params": {…}}}
```

Kinds: `mount`, `unmount`, `initialized`, `tools/call`, `resources/read`,
`model-context`, `message`, `open-link`, `log`, `view-error` (payload
`{code, message}`; codes `sandbox_config`, `sandbox_unreachable`,
`view_init_timeout`, `view_failed`). Every envelope is
validated (`protocol.parse_envelope`): version, kind, id formats, object
payload. `mount_id` must be the widget's current mount and `render_id` its
current render, otherwise the request is rejected as stale.

The handler answers immediately (it runs inside Shiny's message loop, so it
must not block): `{"status": "done", "ok": …, "result"|"error": …}` or
`{"status": "pending"}`. Pending results (MCP calls) run as tasks and arrive
as a custom message:

Python → browser, `session.send_custom_message("shiny-mcp-host", message)`:

```json
{"v": 1, "host_id": "mcp", "widget_id": "w1", "kind": "response",
 "mount_id": "9f3a…", "request_id": "9f3a….3", "payload": {"ok": true, "result": {…}}}
```

Kinds: `render`, `loading`, `error`, `fallback`, `response`, `teardown`.
Every request id is unique per mount; the browser keeps a bounded pending
map (64) with timeouts and ignores responses for other mounts or already
settled ids, so repeated identical payloads are separate events. Python
bounds concurrent View requests per widget (`max_pending_per_widget`) and
cancels (and settles) a widget's in-flight requests on unmount, re-render
and session end.

## Sandbox / origin strategy

Double iframe, as in the upstream basic-host, with stricter defaults:

```
Shiny page (app origin, e.g. http://127.0.0.1:8000)
└─ outer iframe  sandbox="allow-scripts allow-same-origin allow-forms"
   src = <sandbox origin>/sandbox.html?host=<app origin>&nonce=<random>&csp=<json>
   └─ inner iframe  sandbox="allow-scripts allow-forms"   (opaque origin, srcdoc)
      = untrusted View HTML
```

- The sandbox is served by `shiny_mcp_host.sandbox` from a **different
  origin**. `MCPHost` refuses to start without a sandbox URL
  (`SandboxConfigError`) or when it equals the app origin; the browser adapter
  re-checks against `location.origin` and refuses to render (fail closed).
  There is no same-origin fallback.
- The sandbox server only serves `/sandbox.html`, `/sandbox.js` and `/healthz`
  (which identifies it, so `start_sandbox_server` never mistakes another
  process on its port for a sandbox), only for
  allow-listed host origins, sets `frame-ancestors <host origin>`, `no-store`,
  and builds the CSP **HTTP header** from the (Python-filtered, re-validated)
  resource CSP. Defaults are the spec's restrictive policy
  (`default-src 'none'`, `connect-src 'none'`, `frame-src 'none'`,
  `object-src 'none'`, `base-uri 'self'`, plus `form-action 'none'`).
- The View runs **without** `allow-same-origin` by default (opaque origin):
  it cannot reach the proxy DOM, cookies, or storage. `HostPolicy(view_same_origin=True)`
  restores upstream behaviour for Views that need storage; the adapter then
  additionally requires the sandbox to use a different *hostname* than the app,
  because cookies are not isolated by port.
- The outer proxy validates `event.source` and `event.origin` (host origin
  from its URL; inner `"null"` or own origin), drops any
  `ui/notifications/sandbox-*` message coming from the View, accepts a single
  `sandbox-resource-ready`, restricts inner sandbox tokens to
  `allow-scripts allow-forms [allow-same-origin]`, and self-tests that the
  top window is unreachable.
- The host accepts the proxy only after a `sandbox-proxy-ready` carrying the
  per-mount nonce from the expected window and origin, and its AppBridge
  transport (`SandboxTransport`) checks source window **and** origin of every
  message and posts with an explicit target origin.
- Authorisation never depends on the browser: Python enforces the tool
  allowlist / visibility rules, sizes, CSP/permission filtering and link schemes.

### Dev vs production origins

Development: Shiny on `http://127.0.0.1:8000`, sandbox on
`http://localhost:8765` (different origin *and* hostname). Production: serve
`sandbox_app()` from a dedicated domain (e.g.
`https://mcp-sandbox.example-usercontent.com`) over HTTPS, pass that
`sandbox_url`, and set `allowed_host_origins` to your app's exact origin(s).
An HTTPS app requires an HTTPS sandbox. See [deployment.md](deployment.md).

### Without a second hostname

Not implemented as written; recorded here as a fallback for deployments that
can only use one hostname (e.g. a single Posit Connect server without a DNS
alias). What is implemented is the simpler, experimental
[trusted mode](architecture.md#trusted-mode-dangerously_trust_appstrue): the
same opaque-origin `srcdoc` frame and `<meta>` CSP, but holding the View
itself with no proxy, and only for apps you control. The design below
would keep the proxy layer and so be closer to the spec.

Instead of loading the outer proxy from a URL, the host would build it from
inline HTML (`srcdoc`) in an iframe with `sandbox="allow-scripts allow-forms"`
and no `allow-same-origin`. Browsers give such a frame an *opaque* origin,
distinct from every other origin including the Shiny page's, so no sandbox
server or second hostname is needed. Isolation from the Shiny page's DOM,
cookies, storage and inputs is kept, Python still authorises every tool call,
and the host still identifies the proxy by its window object.

Costs, which is why it would be an explicit opt-in and not the default:

- It departs from the spec, which requires the sandbox proxy to have
  `allow-same-origin` (a real origin).
- The CSP is delivered by a `<meta>` tag instead of an HTTP header (still
  enforced and inherited by the View), and `frame-ancestors` is unavailable.
- Views that need a real origin (`localStorage`, cookies) cannot work, and
  `view_same_origin` cannot be offered.
- Browser process isolation of sandboxed frames varies by browser; a separate
  hostname is a separate site and reliably gets its own process.

The malicious-View test suite would have to pass unchanged in this mode.
