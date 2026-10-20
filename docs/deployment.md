# Deploying

## The one requirement: a second hostname for the sandbox

MCP App Views run in a sandbox that must be served from a **different
origin** than your Shiny app, and in production from a different
**hostname**: cookies are shared across ports, and a different path on the
same hostname (`/content/sandbox/` next to `/content/my-app/`) is the same
origin. A second name routed to the same server is enough (a DNS alias, or a
load-balancer rule that *forwards* that name to the server; a *redirect*
does not work, because the browser ends up back on the main hostname). It is
a second **name**, not a second server.

The MCP server and the Shiny app have no such requirement. Only the sandbox
needs its own hostname, because it keeps third-party HTML away from your
page's DOM, cookies and inputs. Without it the host refuses to render. See
[architecture.md](architecture.md#why-the-sandbox-needs-its-own-hostname).

## Local development

`start_sandbox_server(port=8765)` runs the sandbox in a background thread at
`http://localhost:8765`. Open the app at `http://127.0.0.1:…`, so the two
differ by hostname. `make demo` does this for you. Don't use it in production:
it serves on the server's own `localhost`, which users' browsers can't reach.

## The deployment recipe: sales explorer on Posit Connect

> **Status: rehearsed locally, not yet validated on Posit Connect.** The
> bundles, entry points, environment variables and the full browser flow are
> tested locally (`make rehearse-connect`). How Connect routes the sandbox
> alias, which headers it adds, and how authentication behaves are
> assumptions until [posit-connect-validation.md](posit-connect-validation.md)
> has been run on a real server. The [table below](#what-has-been-verified)
> lists which is which.

Three content items on one Connect server, with users' browsers reaching the
sandbox through a second hostname:

| Content | Type, entry point | Users' browsers reach it at |
|---|---|---|
| MCP server | ASGI API, `server:app` | never: only the Shiny app's Python process calls it, at `https://connect.example.com/content/<mcp-guid>/mcp` |
| Sandbox | ASGI API, `app:app` | `https://connect-sandbox.example.com/content/<sandbox-guid>/sandbox.html` (**the alias**) |
| Shiny app | Shiny, `app:app` | `https://connect.example.com/content/<app-guid>/` |

`connect.example.com` is your Connect server; `connect-sandbox.example.com`
is the alias. Vanity URLs should work too (not yet tried); use whatever URL
users' browsers see.

Three items is the baseline, not a browser requirement: isolation needs the
sandbox on its own hostname, not in its own process. Serving the Shiny app and
the sandbox from one application, reached through the two hostnames, is a
possible simplification that is neither implemented nor validated; the
[runbook](posit-connect-validation.md#next-step-one-application-two-hostnames)
records it as the next step.

### 0. Ask your administrator for the alias

- a DNS name (e.g. `connect-sandbox.example.com`) pointing at the Connect
  server (or the proxy in front of it); behind a load balancer such as an AWS
  ALB, a listener rule for that host that **forwards** to Connect's target
  group (not a redirect rule),
- that name on the TLS certificate,
- Connect (and any proxy) serving content under that name **without
  redirecting** to the main hostname, and without adding `X-Frame-Options` or
  a `frame-ancestors` policy that excludes the app to the sandbox's responses.

### 1. Build the three folders

```bash
make setup               # once; no Node needed
make connect-bundles     # -> dist/connect/
```

| Folder | Files | Source |
|---|---|---|
| `dist/connect/mcp-server/` | `server.py`, `view.built.html`, `requirements.txt` (`mcp==2.3.0`), `README.md` | `examples/sales_explorer/server.py`, with the View pre-rendered (`make demo-server-bundle`) |
| `dist/connect/sandbox/` | `app.py`, `requirements.txt`, `shiny_mcp_host-0.1.0-py3-none-any.whl` | `examples/posit_connect/sandbox/app.py` |
| `dist/connect/shiny-app/` | `app.py`, `requirements.txt`, `shiny_mcp_host-0.1.0-py3-none-any.whl` | `examples/sales_explorer/app.py`, unchanged |

`dist/connect/REVISION` records the commit. shiny-mcp-host is not on PyPI, so
the sandbox and Shiny app bundle a wheel built from your checkout, referenced
as `./shiny_mcp_host-…whl` on the first line of `requirements.txt`; every other
dependency is pinned to `uv.lock`. If your server cannot install a local wheel,
`python scripts/package_connect.py --from-git <pushed commit>` references
`shiny-mcp-host @ git+https://github.com/mattmalcher/mcp-app-shiny@<commit>`
instead (the server then needs access to GitHub).

### 2. Deploy

With [`rsconnect-python`](https://docs.posit.co/rsconnect-python/) and an API
key for a publisher account (`CONNECT_SERVER`, `CONNECT_API_KEY` below are
placeholders in your shell, never committed):

```bash
# MCP server (Connect will assign <mcp-guid>)
rsconnect deploy fastapi -s "$CONNECT_SERVER" -k "$CONNECT_API_KEY" \
  --entrypoint server:app --title "Sales explorer MCP server" \
  -E MCP_ALLOWED_HOSTS=connect.example.com \
  dist/connect/mcp-server

# Sandbox
rsconnect deploy fastapi -s "$CONNECT_SERVER" -k "$CONNECT_API_KEY" \
  --entrypoint app:app --title "MCP Apps sandbox" \
  -E SHINY_APP_ORIGIN=https://connect.example.com \
  dist/connect/sandbox

# Shiny app (needs both URLs above)
rsconnect deploy shiny -s "$CONNECT_SERVER" -k "$CONNECT_API_KEY" \
  --entrypoint app:app --title "Sales explorer" \
  -E MCP_SERVER_URL=https://connect.example.com/content/<mcp-guid>/mcp \
  -E SHINY_MCP_SANDBOX_URL=https://connect-sandbox.example.com/content/<sandbox-guid>/sandbox.html \
  -E MCP_SERVER_API_KEY \
  dist/connect/shiny-app
```

(`-E NAME` without a value takes it from your shell, so the key is not on the
command line. `rsconnect deploy fastapi` deploys any ASGI app.)

| Content | Variable | Value |
|---|---|---|
| MCP server | `MCP_ALLOWED_HOSTS` | Host header(s) the server accepts, comma-separated (enables the MCP SDK's DNS-rebinding protection). Unset: protection off. |
| Sandbox | `SHINY_APP_ORIGIN` | **Required.** The Shiny app's origin as browsers see it (`https://connect.example.com`, no path). The sandbox refuses every other embedder; it fails to start without it. |
| Shiny app | `SHINY_MCP_SANDBOX_URL` | The sandbox page **on the alias**. Without it the example starts a local development sandbox, which browsers cannot reach on a server. |
| Shiny app | `MCP_SERVER_URL` | The MCP endpoint, `<MCP content URL>/mcp` |
| Shiny app | `MCP_SERVER_API_KEY` | Optional: sent as `Authorization: Key <value>` to the MCP server. Stays in Python. |

### 3. Access settings

| Content | Access | Why |
|---|---|---|
| Sandbox | **Anyone, no login required** | Browsers carry no Connect login to the alias, so a login requirement would block it. It serves only a static page and script and holds no data. |
| MCP server | Logged-in users or specific users/groups | Only the Shiny app calls it, with `MCP_SERVER_API_KEY` (an API key of an account allowed to view it). |
| Shiny app | As your audience requires | |

### 4. Check it

```bash
shiny-mcp-sandbox --check \
  https://connect-sandbox.example.com/content/<sandbox-guid>/sandbox.html \
  --host-origin https://connect.example.com
```

then open the Shiny app and ask *"Explore Q3 sales in the West"*. For the
complete set of checks (server, browser, isolation, with evidence) see
[posit-connect-validation.md](posit-connect-validation.md).

### What has been verified

| | Locally (`make rehearse-connect`, CI tests) | On Posit Connect |
|---|---|---|
| Each folder installs from its own `requirements.txt` into a clean Python 3.11 venv | ✅ | pending |
| Entry points `server:app`, `app:app` (sandbox), `app:app` (Shiny) start | ✅ (uvicorn) | pending |
| Environment variables wire the three together | ✅ | pending |
| Sandbox on a distinct hostname, headers allow embedding by the app only | ✅ (`localhost` vs `127.0.0.1`) | pending |
| Explorer renders, filter re-queries, selection reaches Python, follow-up answered | ✅ (Chromium) | pending |
| View cannot reach the page, cookies or network; unauthorised tool call refused | ✅ | pending |
| Sandbox works under a path prefix (`/content/<guid>/`) | ✅ (unit test) | pending |
| A load balancer in front of Connect (e.g. an AWS ALB) forwards the alias rather than redirecting | — | **assumption** (check your rules) |
| Connect serves content on the alias without redirecting | — | **assumption** |
| Connect adds no `X-Frame-Options` / `frame-ancestors` to the sandbox | — | **assumption** |
| Connect installs a wheel referenced as `./…whl` in `requirements.txt` | — | **assumption** |
| `MCP_ALLOWED_HOSTS=connect.example.com` matches the Host header the MCP server sees behind Connect | — | **assumption** |
| An API key in `Authorization: Key …` authenticates the Shiny app to the MCP server | — | **assumption** |
| Without a key, Connect refuses the MCP server with 401/403 (or a login redirect) | — | **assumption** |
| Shiny's websocket works for the app (standard Connect behaviour) | — | **assumption** |

Update this table when the runbook has been run.

## Other platforms

Serve `shiny_mcp_host.sandbox.sandbox_app(allowed_host_origins=["https://app.example.com"])`
(or `examples/posit_connect/sandbox/app.py` with `SHINY_APP_ORIGIN`) with any
ASGI server on its own hostname over HTTPS, and pass
`sandbox_url="https://sandbox.example.com/sandbox.html"` (or set
`SHINY_MCP_SANDBOX_URL`). An HTTPS app requires an HTTPS sandbox. The same
`--check` and `scripts/check_deployment.py` work against any host.

## Diagnosing sandbox problems

Two different questions, checked by two different tools:

1. **Does the server answer correctly?** `shiny-mcp-sandbox --check
   SANDBOX_URL --host-origin APP_ORIGIN` (or `check_sandbox()`), from any
   machine. It reports DNS and TLS failures, redirects (e.g. the alias
   redirecting to the main hostname or a login page, which is what a load
   balancer *redirect* rule does: it must *forward* instead), authentication
   (401/403), an app origin the sandbox does not allow, and headers that stop
   framing (`X-Frame-Options`, or any `frame-ancestors` policy, e.g. one added
   by a proxy, whose sources do not match the app's origin). A source form it
   does not model is reported `INCONCLUSIVE`, which does not count as a pass.
2. **Does the browser embed it?** Only a browser can tell. The widget's
   `data-error-code` says where it stopped:

   | `data-error-code` | Meaning | Look at |
   |---|---|---|
   | `sandbox_config` | Refused before loading: the sandbox URL is the app's origin, or http under https | `SHINY_MCP_SANDBOX_URL` |
   | `sandbox_unreachable` | The proxy page never signalled ready | `--check` first; if it passes, the browser console (blocked frame, mixed content, TLS, network path from the user's machine) |
   | `view_init_timeout` | The proxy loaded; the View never initialized | The MCP server's View HTML, browser console |
   | `view_failed` | Other browser-side failure | Browser console (`[shiny-mcp-host]`), Python log |

   `scripts/check_deployment.py` runs both levels and the functional flow,
   and saves the evidence.

## Transports and authentication

Streamable HTTP (`url=`) and stdio (`transport="stdio"`). For authenticated
servers pass `headers=` or an `httpx2.Auth` as `auth=` to `add_server`; OAuth
flows are not built in. Credentials stay in Python.
