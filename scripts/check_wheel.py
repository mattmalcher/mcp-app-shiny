"""A13: run inside a *clean* venv where only the built wheel is installed.

Verifies the bundled JS/CSS ship in the wheel and are actually served:
the Shiny HTML dependency resolves, and the sandbox server serves sandbox.js.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from shiny import App, ui
from starlette.testclient import TestClient

import shiny_mcp_host
from shiny_mcp_host import mcp_widget
from shiny_mcp_host.sandbox import sandbox_app

pkg = Path(shiny_mcp_host.__file__).parent
assert "site-packages" in str(pkg), f"not testing an installed package: {pkg}"
for name in ("shiny-mcp-host.js", "shiny-mcp-host.css", "sandbox.js", "mcp-app-sdk.js"):
    f = pkg / "static" / name
    assert f.is_file() and f.stat().st_size > 500, f"missing static asset {f}"

app = App(ui.page_fluid(mcp_widget("w")), lambda i, o, s: None)
with TestClient(app) as client:
    html = client.get("/").text
    assert "<shiny-mcp-widget" in html
    srcs = re.findall(r'src="([^"]*shiny-mcp-host[^"]*\.js)"', html)
    hrefs = re.findall(r'href="([^"]*shiny-mcp-host[^"]*\.css)"', html)
    assert srcs and hrefs, "html dependency not rendered"
    for url in srcs + hrefs:
        r = client.get("/" + url.lstrip("/"))
        assert r.status_code == 200 and len(r.content) > 500, (url, r.status_code)

from shiny_mcp_host.view import app_html  # noqa: E402

assert "globalThis.McpApps=" in app_html(body="", script="")

sb = TestClient(sandbox_app(), base_url="http://localhost:8765")
r = sb.get("/sandbox.js")
assert r.status_code == 200 and len(r.content) > 500
print(f"wheel OK: shiny_mcp_host {shiny_mcp_host.__version__} from {pkg} (python {sys.version.split()[0]})")
