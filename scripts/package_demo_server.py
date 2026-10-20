"""Assemble the sales explorer MCP server into a self-contained, Node-free folder.

    python scripts/package_demo_server.py [OUT_DIR]   (default: dist/sales-explorer-mcp-server)

The folder holds server.py, requirements.txt and view.built.html (the View with
the MCP Apps SDK already inlined), so it needs neither Node nor shiny-mcp-host:
deployable as-is (e.g. to Posit Connect, entry point ``server:app``). CI
publishes it as the ``sales-explorer-mcp-server`` artifact.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "examples/sales_explorer"
FILES = ["server.py", "requirements.txt"]

README = """# Sales explorer MCP server

A self-contained MCP server with an interactive MCP App (built by shiny-mcp-host CI).
Self-contained: view.built.html is the complete View (official MCP Apps SDK inlined).
Its sources live in shiny-mcp-host's examples/sales_explorer/.

Run locally:  pip install -r requirements.txt && python server.py --port 3001
Hosted:       ASGI entry point `server:app`, MCP endpoint at <content URL>/mcp
              (e.g. Posit Connect: rsconnect deploy fastapi --entrypoint server:app .)
              Set MCP_ALLOWED_HOSTS=<your hostname> to enable DNS-rebinding protection.
"""


def package(out: Path) -> Path:
    sys.path.insert(0, str(SRC))
    from server import view_html  # type: ignore[import-not-found]

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(SRC / name, out / name)
    (out / "view.built.html").write_text(view_html(), encoding="utf-8")
    (out / "README.md").write_text(README, encoding="utf-8")
    return out


if __name__ == "__main__":
    target = package(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist/sales-explorer-mcp-server")
    print(f"packaged {', '.join(sorted(p.name for p in target.iterdir()))} -> {target}")
