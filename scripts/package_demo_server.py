"""Assemble the sales explorer MCP server into a self-contained, Node-free folder.

    python scripts/package_demo_server.py [OUT_DIR]   (default: dist/sales-explorer-mcp-server)

The folder holds server.py, view.html, the prebuilt view.bundle.js and
requirements.txt: deployable as-is (e.g. to Posit Connect, entry point
``server:app``). CI publishes it as the ``sales-explorer-mcp-server`` artifact.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "examples/sales_explorer"
FILES = ["server.py", "view.html", "view.bundle.js", "requirements.txt"]

README = """# Sales explorer MCP server

A self-contained MCP server with an interactive MCP App (built by shiny-mcp-host CI).
No Node needed: the View's JavaScript is prebuilt in view.bundle.js.

Run locally:  pip install -r requirements.txt && python server.py --port 3001
Hosted:       ASGI entry point `server:app`, MCP endpoint at <content URL>/mcp
              (e.g. Posit Connect: rsconnect deploy fastapi --entrypoint server:app .)
              Set MCP_ALLOWED_HOSTS=<your hostname> to enable DNS-rebinding protection.
"""


def package(out: Path) -> Path:
    bundle = SRC / "view.bundle.js"
    if not bundle.exists():
        sys.exit(f"{bundle} is missing: build it first (`cd frontend && npm run build`).")
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in FILES:
        shutil.copy2(SRC / name, out / name)
    (out / "README.md").write_text(README, encoding="utf-8")
    return out


if __name__ == "__main__":
    target = package(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist/sales-explorer-mcp-server")
    print(f"packaged {', '.join(FILES)} -> {target}")
