"""Sandbox proxy server: serves ``sandbox.html`` from an origin distinct from Shiny.

The MCP Apps spec requires web hosts to wrap each View in a sandbox proxy
iframe whose origin differs from the host. This module serves that proxy with
an HTTP ``Content-Security-Policy`` header built from the resource's (already
policy-filtered) ``_meta.ui.csp``; the View inherits it, and it cannot be
tampered with from inside the page.

Run standalone::

    python -m shiny_mcp_host.sandbox --port 8765

or in-process (development)::

    from shiny_mcp_host.sandbox import start_sandbox_server
    sandbox = start_sandbox_server(port=8765)
    MCPHost(session, sandbox_url=sandbox.url)
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import re
import socket
import threading
import time
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from ._version import __version__
from .policy import CSP_KEYS, _is_valid_csp_source

__all__ = (
    "probe_sandbox",
    "sandbox_app",
    "build_csp_header",
    "start_sandbox_server",
    "SandboxServer",
    "DEFAULT_HOST_ORIGINS",
)

logger = logging.getLogger("shiny_mcp_host.sandbox")

STATIC_DIR = Path(__file__).parent / "static"
#: Identifies this service on ``/healthz`` so a busy port can be told apart.
SERVICE_NAME = "shiny-mcp-host-sandbox"

#: Host (Shiny app) origins allowed to embed the sandbox by default: loopback dev servers.
DEFAULT_HOST_ORIGINS: tuple[str, ...] = ("http://127.0.0.1:*", "http://localhost:*")

_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$|^https?://\[::1\](:\d{1,5})?$")

SANDBOX_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<title>MCP App sandbox</title>
<style>
html,body{margin:0;height:100%;width:100%;background:transparent;overflow:hidden}
body{display:flex;flex-direction:column}
iframe{flex:1 1 auto;border:0;width:100%;height:100%;background:transparent;color-scheme:inherit}
</style>
</head>
<body>
<script src="sandbox.js"></script>
</body>
</html>
"""


def _sources(csp: dict[str, Any], key: str) -> list[str]:
    values = csp.get(key)
    if not isinstance(values, list):
        return []
    # Re-validated here: the sandbox does not trust its query string.
    return [v for v in values[:64] if _is_valid_csp_source(v)]


def build_csp_header(csp: dict[str, Any] | None, host_origin: str) -> str:
    """Build the sandbox CSP. Defaults follow the MCP Apps spec's restrictive policy."""
    csp = csp or {}
    resource = " ".join(_sources(csp, "resourceDomains"))
    connect = " ".join(_sources(csp, "connectDomains"))
    frame = " ".join(_sources(csp, "frameDomains"))
    base = " ".join(_sources(csp, "baseUriDomains"))
    directives = [
        "default-src 'none'",
        f"script-src 'self' 'unsafe-inline' {resource}".strip(),
        f"style-src 'self' 'unsafe-inline' {resource}".strip(),
        f"img-src 'self' data: {resource}".strip(),
        f"font-src 'self' data: {resource}".strip(),
        f"media-src 'self' data: {resource}".strip(),
        f"connect-src {connect}" if connect else "connect-src 'none'",
        f"frame-src {frame}" if frame else "frame-src 'none'",
        "object-src 'none'",
        f"base-uri {base}" if base else "base-uri 'self'",
        "form-action 'none'",
        f"frame-ancestors {host_origin}",
    ]
    return "; ".join(directives)


def _origin_allowed(origin: str, patterns: Sequence[str]) -> bool:
    if not _ORIGIN.match(origin):
        return False
    return any(origin == p or ("*" in p and fnmatch.fnmatchcase(origin, p)) for p in patterns)


def sandbox_app(allowed_host_origins: Sequence[str] = DEFAULT_HOST_ORIGINS) -> Starlette:
    """ASGI app serving only ``/sandbox.html`` and ``/sandbox.js``.

    ``allowed_host_origins`` lists the Shiny app origins permitted to embed the
    sandbox (exact origins, or patterns such as ``http://localhost:*``).
    """
    patterns = tuple(allowed_host_origins)

    async def sandbox_html(request: Request) -> Response:
        host = request.query_params.get("host", "")
        nonce = request.query_params.get("nonce", "")
        if not _origin_allowed(host, patterns):
            logger.warning("Refusing sandbox for host origin %r", host)
            return PlainTextResponse("Embedding origin not allowed", status_code=403)
        if not _NONCE.match(nonce):
            return PlainTextResponse("Missing or invalid nonce", status_code=400)
        csp: dict[str, Any] | None = None
        raw = request.query_params.get("csp")
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                return PlainTextResponse("Invalid csp parameter", status_code=400)
            if isinstance(parsed, dict):
                csp = {k: parsed[k] for k in CSP_KEYS if k in parsed}
        own_origin = f"{request.url.scheme}://{request.url.netloc}".lower()
        if own_origin == host.lower():
            return PlainTextResponse("Sandbox origin must differ from the host origin", status_code=403)
        header = build_csp_header(csp, host)
        logger.info("Sandbox CSP for %s: %s", host, header)
        return Response(
            SANDBOX_HTML,
            media_type="text/html",
            headers={
                "Content-Security-Policy": header,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    async def sandbox_js(request: Request) -> Response:
        return FileResponse(
            STATIC_DIR / "sandbox.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )

    async def healthz(request: Request) -> Response:
        return JSONResponse({"service": SERVICE_NAME, "version": __version__})

    async def not_found(request: Request) -> Response:
        return PlainTextResponse("Not found", status_code=404)

    return Starlette(
        routes=[
            Route("/sandbox.html", sandbox_html),
            Route("/sandbox.js", sandbox_js),
            Route("/healthz", healthz),
            Route("/{path:path}", not_found),
        ]
    )


@dataclass
class SandboxServer:
    """A sandbox server running in a background thread."""

    host: str
    port: int
    public_host: str
    _server: Any = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    @property
    def origin(self) -> str:
        return f"http://{self.public_host}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/sandbox.html"

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        _RUNNING.pop(self.port, None)


_RUNNING: dict[int, SandboxServer] = {}
_LOCK = threading.Lock()


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex((host, port)) == 0


def probe_sandbox(host: str, port: int, timeout: float = 2.0) -> str | None:
    """Return the version of the shiny-mcp-host sandbox on ``host:port``, or ``None``."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=timeout) as r:
            data = json.loads(r.read(4096))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("service") == SERVICE_NAME:
        return str(data.get("version", "?"))
    return None


def start_sandbox_server(
    port: int = 8765,
    *,
    host: str = "127.0.0.1",
    public_host: str = "localhost",
    allowed_host_origins: Sequence[str] = DEFAULT_HOST_ORIGINS,
) -> SandboxServer:
    """Start (once per process and port) a sandbox server in a daemon thread.

    ``public_host`` is the hostname browsers use to reach it. The default,
    ``localhost``, gives a distinct origin *and* a distinct cookie jar when the
    Shiny app is opened at ``127.0.0.1``. Intended for local development; in
    production serve :func:`sandbox_app` from a dedicated domain.
    """
    import uvicorn

    with _LOCK:
        existing = _RUNNING.get(port)
        if existing is not None:
            return existing
        if _port_open(host, port):
            # Reuse a sandbox another process serves (e.g. the parent of a
            # --reload worker), but only after confirming that is what it is.
            version = probe_sandbox(host, port)
            if version is None:
                raise RuntimeError(
                    f"Port {port} on {host} is in use, but not by a shiny-mcp-host sandbox "
                    f"(no valid http://{host}:{port}/healthz). Stop that process or pick another "
                    "port, e.g. start_sandbox_server(port=8766) / SANDBOX_PORT=8766."
                )
            logger.info("Reusing shiny-mcp-host sandbox %s on %s:%d", version, host, port)
            server = SandboxServer(host, port, public_host)
            _RUNNING[port] = server
            return server
        config = uvicorn.Config(
            sandbox_app(allowed_host_origins), host=host, port=port, log_level="warning", lifespan="off"
        )
        uv = uvicorn.Server(config)
        thread = threading.Thread(target=uv.run, name=f"shiny-mcp-sandbox:{port}", daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not uv.started and time.monotonic() < deadline and thread.is_alive():
            time.sleep(0.02)
        if not uv.started:
            raise RuntimeError(f"Sandbox server failed to start on {host}:{port}")
        server = SandboxServer(host, port, public_host, uv, thread)
        _RUNNING[port] = server
        return server


def main(argv: Sequence[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Serve the shiny-mcp-host sandbox proxy.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allow-host-origin",
        action="append",
        dest="origins",
        help="Shiny app origin allowed to embed the sandbox (repeatable; supports '*' "
        f"port wildcards). Default: {' '.join(DEFAULT_HOST_ORIGINS)}",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(sandbox_app(args.origins or DEFAULT_HOST_ORIGINS), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
