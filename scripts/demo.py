"""One-command development demo: MCP server + sandbox + Shiny app.

    python scripts/demo.py [sales|clock|standalone]   (make demo / demo-clock / demo-standalone)

Checks prerequisites and ports first, prints what is running where, and stops
everything on Ctrl+C.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL_SERVER = ROOT / "frontend/node_modules/@modelcontextprotocol/server-basic-vanillajs/dist/index.js"
EXAMPLES = {
    "sales": (
        "examples/sales_explorer/app.py",
        "Sales explorer MCP server (examples/sales_explorer/server.py)",
    ),
    "clock": ("examples/chat/app.py", "Official ext-apps 'Get Server Time' server (npm, unmodified)"),
    "standalone": (
        "examples/standalone/app.py",
        "Official ext-apps 'Get Server Time' server (npm, unmodified)",
    ),
}


def fail(message: str) -> None:
    print(f"\n✗ {message}\n", file=sys.stderr)
    sys.exit(1)


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_for(port: int, proc: subprocess.Popen, name: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            fail(f"{name} exited with code {proc.returncode} (see output above).")
        if port_in_use(port):
            return
        time.sleep(0.1)
    fail(f"{name} did not start listening on port {port} within {timeout:.0f}s.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("example", nargs="?", default="sales", choices=sorted(EXAMPLES))
    parser.add_argument("--mcp-port", type=int, default=int(os.environ.get("DEMO_PORT", 3001)))
    parser.add_argument("--app-port", type=int, default=int(os.environ.get("APP_PORT", 8000)))
    parser.add_argument("--sandbox-port", type=int, default=int(os.environ.get("SANDBOX_PORT", 8765)))
    args = parser.parse_args()
    app_path, server_label = EXAMPLES[args.example]

    # Prerequisites, with the fix for each.
    if not (ROOT / "src/shiny_mcp_host/static/shiny-mcp-host.js").exists():
        fail("Browser bundle missing from src/shiny_mcp_host/static. Run `make setup-dev` to rebuild it.")
    if args.example == "sales":
        mcp_cmd = [
            sys.executable,
            str(ROOT / "examples/sales_explorer/server.py"),
            "--port",
            str(args.mcp_port),
        ]
    else:
        if shutil.which("node") is None:
            fail(
                f"The {args.example} demo runs the official ext-apps server, which needs Node 20+ "
                "(`make demo` doesn't)."
            )
        if not OFFICIAL_SERVER.exists():
            fail("Official demo server not installed. Run `make setup-dev` (npm ci in frontend/).")
        mcp_cmd = ["node", str(OFFICIAL_SERVER)]
    for port, what, flag in [
        (args.mcp_port, "MCP server", "--mcp-port / DEMO_PORT"),
        (args.app_port, "Shiny app", "--app-port / APP_PORT"),
    ]:
        if port_in_use(port):
            fail(
                f"Port {port} (for the {what}) is already in use. "
                f"Stop that process or choose another with {flag}."
            )

    env = {
        **os.environ,
        "PORT": str(args.mcp_port),
        "MCP_SERVER_URL": f"http://127.0.0.1:{args.mcp_port}/mcp",
        "SANDBOX_PORT": str(args.sandbox_port),
    }
    procs: list[subprocess.Popen] = []

    def stop(*_: object) -> None:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    procs.append(subprocess.Popen(mcp_cmd, cwd=ROOT, env=env))
    wait_for(args.mcp_port, procs[-1], "MCP server")
    shiny_cmd = [
        sys.executable,
        "-m",
        "shiny",
        "run",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.app_port),
        app_path,
    ]
    procs.append(subprocess.Popen(shiny_cmd, cwd=ROOT, env=env))
    wait_for(args.app_port, procs[-1], "Shiny app")
    wait_for(args.sandbox_port, procs[-1], "Sandbox (started by the Shiny app)")

    print(
        f"""
  shiny-mcp-host demo: {args.example}

    MCP server  http://127.0.0.1:{args.mcp_port}/mcp   {server_label}
    Sandbox     http://localhost:{args.sandbox_port}        separate origin for the untrusted app HTML
    Shiny app   http://127.0.0.1:{args.app_port}        <- open this (use 127.0.0.1, not localhost)

  Ctrl+C stops everything.
""",
        flush=True,
    )
    while all(p.poll() is None for p in procs):
        time.sleep(0.5)
    fail("A demo process exited unexpectedly (see output above).")


if __name__ == "__main__":
    main()
