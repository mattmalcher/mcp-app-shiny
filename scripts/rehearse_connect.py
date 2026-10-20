"""Rehearse the Posit Connect deployment locally, then check it like a real one.

    python scripts/rehearse_connect.py [BUNDLE_DIR]      (make rehearse-connect; default dist/connect)

For each folder made by package_connect.py: create a clean virtualenv, install
only that folder's requirements.txt (from inside the folder, as a server
would), and start its entry point with the environment variables
docs/deployment.md sets on Connect. The three run on separate ports, with the
sandbox reached as ``localhost`` and the others as ``127.0.0.1``, so the
sandbox has its own hostname. Then scripts/check_deployment.py runs every
server, browser and isolation check against them.

This proves the bundles, entry points, environment variables and the flow. It
does not exercise Connect itself (routing, content URLs, authentication, the
sandbox alias, TLS, proxy headers): see docs/posit-connect-validation.md.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENVS = ROOT / ".cache/connect-rehearsal"
LOGS = ROOT / "test-results/connect-rehearsal"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def venv(name: str, folder: Path) -> Path:
    target = VENVS / name
    shutil.rmtree(target, ignore_errors=True)
    subprocess.run(["uv", "venv", "-q", "-p", "3.11", str(target)], check=True)
    py = target / "bin/python"
    subprocess.run(
        ["uv", "pip", "install", "-q", "-p", str(py), "-r", "requirements.txt"], cwd=folder, check=True
    )
    return py


def start(name: str, cmd: list[str], folder: Path, port: int, env: dict[str, str]) -> subprocess.Popen[bytes]:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{name}.log", "wb")  # noqa: SIM115 - closed with the process
    proc = subprocess.Popen(
        cmd,
        cwd=folder,
        env={**os.environ, **env},
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"{name} exited with {proc.returncode}; see {LOGS / (name + '.log')}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                print(f"started {name} on port {port}")
                return proc
        time.sleep(0.2)
    sys.exit(f"{name} did not listen on {port}")


def main() -> None:
    bundles = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist/connect"
    if not (bundles / "shiny-app/app.py").exists():
        sys.exit(f"No bundles in {bundles}; run `make connect-bundles` first.")
    ports = {name: free_port() for name in ("mcp-server", "sandbox", "shiny-app")}
    app_origin = f"http://127.0.0.1:{ports['shiny-app']}"
    sandbox_url = f"http://localhost:{ports['sandbox']}/sandbox.html"
    mcp_url = f"http://127.0.0.1:{ports['mcp-server']}/mcp"
    # The same entry points and environment variables docs/deployment.md uses on Connect.
    plan = {
        "mcp-server": ("server:app", {"MCP_ALLOWED_HOSTS": f"127.0.0.1:{ports['mcp-server']}"}),
        "sandbox": ("app:app", {"SHINY_APP_ORIGIN": app_origin}),
        "shiny-app": ("app:app", {"MCP_SERVER_URL": mcp_url, "SHINY_MCP_SANDBOX_URL": sandbox_url}),
    }
    procs: list[subprocess.Popen[bytes]] = []
    try:
        for name, (entry, env) in plan.items():
            folder = bundles / name
            print(f"installing {folder}/requirements.txt into a clean venv…")
            py = venv(name, folder)  # uvicorn comes with the requirements (mcp and shiny need it)
            cmd = [str(py), "-I", "-m", "uvicorn", entry, "--host", "127.0.0.1", "--port", str(ports[name])]
            procs.append(start(name, cmd, folder, ports[name], env))
        check = [
            sys.executable,
            str(ROOT / "scripts/check_deployment.py"),
            "--app-url",
            f"{app_origin}/",
            "--sandbox-url",
            sandbox_url,
            "--mcp-url",
            mcp_url,
            "--evidence-dir",
            str(LOGS / "evidence"),
        ]
        code = subprocess.run(check).returncode
    finally:
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
    sys.exit(code)


if __name__ == "__main__":
    main()
