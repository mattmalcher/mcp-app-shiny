# Shiny in Shiny (spike)

An MCP App whose View is **itself a Shiny for Python app**, shown in a Shiny chat.

```bash
make demo-shiny      # MCP server + sandbox + host app; open http://127.0.0.1:8000
```

Try *Plot iris*, move the inner app's controls (the sidebar shows what the host
received), then *Switch to cars, colour by cylinders*, *Show mpg against weight*,
*What am I looking at?*, or the inner app's **Ask about this plot** button.

## The idea

A Shiny app is a page plus a Python session, connected by a websocket. An MCP
App View can't open a websocket (the sandbox allows no network by default), but
it can call app-only tools on its own server through the host. So the
websocket is replaced by tool calls:

```
Host page (outer Shiny app, ui.Chat)
└─ sandbox iframe → View: inner Shiny app's page (shiny.js, Bootstrap, … all inlined)
     shiny.js ── Shiny.createSocket() = fake socket
       send(msg)  → app.callServerTool("shiny_rpc", {op: "send", sid, msgs})   returns at once
       onmessage  ← app.callServerTool("shiny_rpc", {op: "poll", sid})         long-poll, always in flight
                        │  through the host: HostPolicy checks every call
                        ▼
MCP server: shiny_rpc → McpConnection (a shiny Connection) → a real shiny AppSession
```

Shiny already supports both ends of this: `shiny.js` uses `Shiny.createSocket`
when it's set, and `AppSession` takes any `Connection` (`send`, `receive`,
`close`). Server output is numbered so the browser can put it back in order.

| File | What it is |
|---|---|
| `shiny_view.py` | The reusable part: `ShinyView` (inlined page, session RPC, idle reaping, `run_in`), `McpConnection`, the browser shim, and `view(session)` for the inner server function |
| `inner_app.py` | The inner Shiny app: iris / cars scatter plot (hand-drawn SVG, no plotting library) |
| `server.py` | MCP server: `explore_dataset` (opens the View), `set_plot_controls` (the model moves the controls), `shiny_rpc` (app-only transport) |
| `app.py` | The host: a Shiny chat with a rule-based stand-in for an LLM |

## The two directions

- **Model → controls.** `explore_dataset`'s arguments become the starting
  controls. Later, `set_plot_controls(session, …)` finds the live inner session
  and sets `view(session).tool_input`. An effect in the inner app moves the
  inputs with `ui.update_select`, and the update reaches the browser through
  the long-poll.
- **Controls → model.** When an input changes, the inner app calls
  `view(session).update_model_context(data, text)`. That is a Shiny custom
  message, which the shim forwards as MCP Apps `ui/update-model-context`. The
  data includes the session id, which is how the host knows what to pass to
  `set_plot_controls`. `send_message` works the same way.

## Limitations (it's a spike)

- **Output types that fetch from the session's HTTP routes don't work:**
  `render.plot` images, downloads, and dynamic UI that adds new HTML
  dependencies. The plot here is SVG sent through `render.ui`.
- **One process.** The inner sessions live in the MCP server's memory, so it
  must be a single worker, not stateless. A session is closed after 60s
  without calls, which is how a closed View gets cleaned up.
- **Session ids are bearer tokens.** Any client of the MCP server that knows
  one can drive that session.
- **Latency.** Every input change is a tool call through the host. Fine for
  dropdowns; untested with sliders.
- **Page size.** The View is about 2 MB, because Bootstrap, jQuery, `shiny.js`
  and the other dependencies are all inlined. The limit is 5 MB
  (`HostPolicy.max_resource_bytes`).
- **Private Shiny APIs.** It uses `App._create_session`, `AppSession._run` and
  `shiny.reactive._core.lock/flush`, so it's pinned to how shiny 1.x works today.
- **Fonts.** Bootstrap's web fonts are not inlined, so the inner app falls
  back to system fonts.

Set `SHINY_VIEW_DEBUG=1` to have the inner app print every message it sends
and receives.

Data: `iris` (Fisher/Anderson) and `cars` (StatLib 1983 ASA Data Expo),
converted to CSV from the copies in [vega-datasets](https://github.com/vega/vega-datasets).
