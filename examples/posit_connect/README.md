# Posit Connect deployment example

The sales explorer deployed as three Connect content items. Only
`sandbox/app.py` is specific to deployment; the MCP server and the Shiny app
are the maintained [`sales_explorer`](../sales_explorer/) example, unchanged.

```bash
make connect-bundles     # -> dist/connect/{mcp-server,sandbox,shiny-app}
make rehearse-connect    # run all three locally from clean venvs and check them
```

Walkthrough: [docs/deployment.md](../../docs/deployment.md).
Validation on a real Connect server: [docs/posit-connect-validation.md](../../docs/posit-connect-validation.md).
