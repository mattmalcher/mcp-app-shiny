"""Start/stop helper processes for integration and e2e tests (always terminated)."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
LOG_DIR = ROOT / "test-results" / "logs"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, proc: subprocess.Popen[bytes], timeout: float = 30, name: str = "") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{name} exited early with code {proc.returncode}; see {LOG_DIR}")
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"{name} did not listen on {port} within {timeout}s")


class Proc:
    def __init__(
        self, name: str, cmd: Sequence[str], port: int, env: dict[str, str] | None = None, cwd: Path = ROOT
    ):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.port = port
        self.log_path = LOG_DIR / f"{name}-{port}.log"
        self._log = open(self.log_path, "wb")  # noqa: SIM115
        self.proc = subprocess.Popen(
            list(cmd),
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdout=self._log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            wait_for_port(port, self.proc, name=name)
        except Exception:
            self.stop()
            raise

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self.proc.wait(timeout=5)
        self._log.close()


def fixture_server(port: int) -> Proc:
    return Proc(
        "fixture-server",
        [sys.executable, str(ROOT / "tests/fixtures/fixture_server.py"), "--port", str(port)],
        port,
    )


def demo_server(port: int) -> Proc:
    """The unmodified official ext-apps example server, installed from npm."""
    bin_path = FRONTEND / "node_modules/@modelcontextprotocol/server-basic-vanillajs/dist/index.js"
    if not bin_path.exists():
        raise RuntimeError("Run `make setup` (npm ci in frontend/) to install the official demo server")
    return Proc("demo-server", ["node", str(bin_path)], port, env={"PORT": str(port)})


def sandbox_server(port: int) -> Proc:
    return Proc("sandbox", [sys.executable, "-m", "shiny_mcp_host.sandbox", "--port", str(port)], port)


def shiny_app(app_path: Path, port: int, env: dict[str, str]) -> Proc:
    return Proc(
        f"shiny-{app_path.parent.name}-{app_path.stem}",
        [sys.executable, "-m", "shiny", "run", "--host", "127.0.0.1", "--port", str(port), str(app_path)],
        port,
        env=env,
    )
