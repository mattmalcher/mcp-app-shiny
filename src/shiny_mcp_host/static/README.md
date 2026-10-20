# `static/`: browser files shipped in the Python package

Everything here is **generated or vendored. Do not edit these files by hand.**
They are committed so that installing and using shiny-mcp-host never needs
Node. CI rebuilds them and fails if the committed copies are out of date.

| File | What it is | Where it comes from | How to regenerate |
|---|---|---|---|
| `shiny-mcp-host.js` | Browser adapter: the `<shiny-mcp-widget>` element, which runs the official `AppBridge` and relays View requests to Python | Our TypeScript in `frontend/src/` (`host.ts`, `sandbox.ts`, `shiny-transport.ts`, `validation.ts`), bundled and minified by esbuild together with the npm packages it imports | `make setup-node` (or `cd frontend && npm run build`); needs Node 20+ |
| `sandbox.js` | The sandbox proxy script, served by `shiny_mcp_host.sandbox` on the sandbox's own origin | Our `frontend/src/sandbox-proxy.ts` and `validation.ts`, bundled the same way | Same |
| `shiny-mcp-host.css` | Widget styles | Copied from `frontend/src/styles.css` | Same |
| `THIRD_PARTY_LICENSES.md` | License texts of every npm package bundled into the two `.js` files above | Written by `frontend/build.mjs` from esbuild's list of bundled inputs | Same |
| `mcp-app-sdk.js` | The official MCP Apps JavaScript SDK, inlined into Views by `shiny_mcp_host.view.app_html()` as `McpApps` | `@modelcontextprotocol/ext-apps` from npm (`dist/src/app-with-deps.js`, version pinned in `frontend/package.json`), integrity-checked; the only change is its final `export{…}` becoming `globalThis.McpApps={…}`. Its license is in its header comment. | `make vendor-sdk` (Python only, no Node); `scripts/vendor_app_sdk.py --check` verifies it against upstream |

## Licenses

shiny-mcp-host's own code is MIT (see `LICENSE` at the repository root).

The bundles also contain third-party code, all under the MIT License:
`@modelcontextprotocol/client`, `@modelcontextprotocol/core` and
`@modelcontextprotocol/ext-apps` (the official MCP SDKs), `zod`, `jose`,
`eventsource`, `eventsource-parser` and `pkce-challenge`. Their versions and
full license texts are in `THIRD_PARTY_LICENSES.md`. That is why MIT notices
appear inside the minified files.

The MCP packages' license files explain that the MCP project is moving from
MIT to Apache-2.0: new contributions are Apache-2.0, and older ones remain MIT
until their authors relicense. Their `package.json` still declares MIT. The
full text ships with each copy: in `THIRD_PARTY_LICENSES.md` for the bundles,
and in the header of `mcp-app-sdk.js`.

## Why the bundles are large

Each bundle includes the MCP client, core and Apps packages and `zod`, which
`AppBridge` uses to validate messages. They are minified and loaded once per
page (`shiny-mcp-host.js`) or once per View (`sandbox.js`).
