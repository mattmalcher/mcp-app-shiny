# Development

You need [uv](https://docs.astral.sh/uv/). Node 20+ is only needed to change
the browser code.

```bash
make setup-dev         # Python env + Playwright Chromium (no Node): enough for the pytest suites
make setup-node        # npm ci + browser builds (needs Node 20+)
make lint              # ruff + TypeScript type check
make test-unit         # pytest unit + Vitest
make test-integration  # Python host against real MCP servers
make test-e2e          # Shiny apps in Playwright (Chromium)
make test-security     # every test marked `security`
make test-compat       # same official app in the upstream ext-apps basic-host
make test-dist         # build the wheel, install it in a clean venv and check it
make test              # all of the above (CI also runs `make rehearse-connect`)
make build             # browser bundle + wheel/sdist into dist/ (needs Node)
make vendor-sdk        # update the bundled MCP Apps SDK from npm (Python only)
make connect-bundles   # Posit Connect deployment folders in dist/connect
make rehearse-connect  # run them locally from clean venvs + scripts/check_deployment.py
```

Node is needed to change the host's TypeScript (`frontend/`, whose builds are
committed to `src/shiny_mcp_host/static/`; CI fails if they are stale), for
Vitest and lint's type check (so for `make lint`, `make test-unit` and
`make build`), and for the tests that run the official npm demo server or
upstream basic-host. Without it the official-server tests skip
(set `REQUIRE_OFFICIAL=1` to make them fail instead).

## Layout

| Path | |
|---|---|
| `src/shiny_mcp_host/` | The package. `host.py` (`MCPHost`), `_widgets.py` (per-widget state), `client.py` (MCP connections), `policy.py`, `protocol.py`, `sandbox.py`, `view.py` (`app_html`), `components.py` (`mcp_widget`) |
| `src/shiny_mcp_host/static/` | Generated browser bundles, their third-party license texts, and the vendored MCP Apps SDK; [its README](../src/shiny_mcp_host/static/README.md) says where each file comes from |
| `frontend/` | TypeScript for the browser adapter and sandbox proxy |
| `examples/` | Runnable examples; see [examples/README.md](../examples/README.md) |
| `tests/` | `unit/`, `integration/` (real MCP servers, simulated browser), `e2e/` (Playwright) |
| `scripts/` | Demo launcher, packaging, deployment checks |
| `docs/` | This documentation |

See [architecture.md](architecture.md) for how the pieces fit and
[protocol-contract.md](protocol-contract.md) for the wire formats and pinned
upstream versions.

## Debugging

See [api.md § Debugging](api.md#debugging).
