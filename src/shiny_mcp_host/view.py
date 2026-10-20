"""Write MCP App Views without Node: the official SDK, bundled with this package.

For MCP *server* authors. Importing this module does not import Shiny.

    from shiny_mcp_host.view import app_html

    html = app_html(
        body='<button id="go">Refresh</button> <output id="out"></output>',
        script='''
            const app = new McpApps.App({name: "My view", version: "1.0.0"});
            app.ontoolresult = (r) => { out.textContent = r.content[0].text; };
            go.onclick = async () => { const r = await app.callServerTool({name: "my_tool", arguments: {}});
                                       out.textContent = r.content[0].text; };
            app.connect();
        ''',
    )

Serve ``html`` as your ``ui://`` resource (e.g. ``Apps.add_html_resource``).
``McpApps`` exposes everything the official ``@modelcontextprotocol/ext-apps``
package exports (``App``, ``applyDocumentTheme``, ...); see its documentation.
"""

from __future__ import annotations

from functools import cache
from html import escape
from pathlib import Path

__all__ = ("app_html", "sdk_script", "SDK_VERSION")

_SDK_FILE = Path(__file__).parent / "static" / "mcp-app-sdk.js"


@cache
def sdk_script() -> str:
    """The vendored SDK as JavaScript text; defines ``globalThis.McpApps`` when run as a module."""
    return _SDK_FILE.read_text(encoding="utf-8")


def _sdk_version() -> str:
    first = sdk_script().split("\n", 1)[0]  # "/*! @modelcontextprotocol/ext-apps@X.Y.Z (...)"
    return first.split("@modelcontextprotocol/ext-apps@", 1)[1].split(" ", 1)[0]


SDK_VERSION = _sdk_version()


def _inline(js: str) -> str:
    # Keep script text from closing its own <script> element.
    return js.replace("</script", "<\\/script").replace("<!--", "<\\!--")


def app_html(*, body: str, script: str, title: str = "MCP App", head: str = "") -> str:
    """Build a self-contained MCP App View as one HTML document.

    ``body`` is your markup, ``head`` extra head content (e.g. ``<style>``), and
    ``script`` your JavaScript. It runs as a module after the SDK, with the
    official SDK available as ``McpApps``. Nothing is fetched from the network,
    which suits the sandbox's default Content Security Policy.
    """
    return (
        '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n'
        f"<title>{escape(title)}</title>\n{head}\n</head>\n<body>\n{body}\n"
        f'<script type="module">{_inline(sdk_script())}</script>\n'
        f'<script type="module">\n{_inline(script)}\n</script>\n'
        "</body>\n</html>\n"
    )
