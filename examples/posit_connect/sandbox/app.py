"""The MCP Apps sandbox as a standalone ASGI app, for Posit Connect (or any ASGI host).

Serve it on its own hostname, separate from the Shiny app's. Configuration:

- SHINY_APP_ORIGIN  required: the Shiny app's origin as users' browsers see it,
                    e.g. https://connect.example.com (comma-separate several).
                    Only these origins may embed the sandbox.

Entry point ``app:app``. See docs/deployment.md.
"""

import os

from shiny_mcp_host.sandbox import sandbox_app

origins = [o.strip().rstrip("/") for o in os.environ.get("SHINY_APP_ORIGIN", "").split(",") if o.strip()]
if not origins:
    raise RuntimeError(
        "Set SHINY_APP_ORIGIN to the Shiny app's origin as browsers see it, e.g. https://connect.example.com"
    )

app = sandbox_app(allowed_host_origins=origins)
