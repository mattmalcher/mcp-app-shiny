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

Check a deployed sandbox from the command line (server and headers only; it
cannot tell whether a browser will embed it)::

    shiny-mcp-sandbox --check https://sandbox.example.com/sandbox.html \
        --host-origin https://app.example.com
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import re
import secrets
import socket
import ssl
import threading
import time
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from ._version import __version__
from .policy import CSP_KEYS, _is_valid_csp_source

__all__ = (
    "check_sandbox",
    "frame_ancestors_verdict",
    "SandboxCheck",
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


@dataclass(frozen=True)
class SandboxCheck:
    """One finding of :func:`check_sandbox`.

    ``inconclusive`` findings are not passes (``ok`` is ``False``): the check
    could not decide, e.g. a header used a form it does not model.
    """

    name: str
    ok: bool
    detail: str
    inconclusive: bool = False

    @property
    def label(self) -> str:
        return "PASS" if self.ok else "INCONCLUSIVE" if self.inconclusive else "FAIL"


_DEFAULT_PORTS = {"http": 80, "https": 443}
_HOST_SOURCE = re.compile(
    r"^(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?"
    r"(?P<host>\*|(?:\*\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*|\[[0-9a-f:.]+\])"
    r"(?::(?P<port>\d{1,5}|\*))?(?P<path>/\S*)?$"
)


def _source_matches(source: str, url_origin: str, self_origin: str) -> bool | None:
    """Does one CSP ``frame-ancestors`` source expression match an origin?

    Follows CSP Level 3 matching for the forms ``frame-ancestors`` allows
    (``*``, ``'self'``, ``'none'``, scheme-sources and host-sources, including
    ``*.`` host and ``*`` port wildcards and the http -> https upgrade).
    Returns ``None`` for anything it does not model (e.g. a host-source with a
    path), so callers report the result as inconclusive rather than guess.
    """
    s = source.strip().lower()
    url = urlsplit(url_origin)
    scheme, host = url.scheme, (url.hostname or "")
    port = url.port or _DEFAULT_PORTS.get(scheme)
    if s == "*":
        return scheme in ("http", "https")
    if s == "'self'":
        return url_origin == self_origin
    if s.startswith("'"):  # 'none' and keywords that never match in frame-ancestors
        return False
    if re.fullmatch(r"[a-z][a-z0-9+.-]*:", s):  # scheme-source, e.g. https:
        return s[:-1] == scheme or (s == "http:" and scheme == "https")
    m = _HOST_SOURCE.match(s)
    if m is None or (m["path"] or "/") != "/":
        return None
    src_scheme = m["scheme"] or urlsplit(self_origin).scheme
    if not (src_scheme == scheme or (src_scheme == "http" and scheme == "https")):
        return False
    src_host = m["host"].strip("[]")
    if src_host == "*":
        pass
    elif src_host.startswith("*."):
        if not host.endswith(src_host[1:]):
            return False
    elif src_host != host:
        return False
    if m["port"] == "*":
        return True
    if m["port"] is None:
        return port == _DEFAULT_PORTS.get(scheme)
    src_port = int(m["port"])
    return src_port == port or (src_port == 80 and port == 443 and scheme == "https")


def frame_ancestors_verdict(policies: Sequence[str], host_origin: str, self_origin: str) -> tuple[str, str]:
    """Whether every CSP policy lets ``host_origin`` embed a page served from ``self_origin``.

    ``policies`` are ``Content-Security-Policy`` header values (comma-joined
    policies are split). Every policy is enforced, and within a policy only its
    first ``frame-ancestors`` directive counts. Returns ``(verdict, detail)``
    with verdict ``"allows"``, ``"blocks"``, ``"inconclusive"`` or ``"absent"``
    (no policy restricts framing).
    """
    found: list[str] = []
    verdicts: list[str] = []
    for header in policies:
        for policy in header.split(","):
            directive = next(
                (d.strip() for d in policy.split(";") if d.strip().lower().startswith("frame-ancestors")),
                None,
            )
            if directive is None:
                continue
            sources = directive.split()[1:]
            results = [_source_matches(src, host_origin, self_origin) for src in sources]
            verdict = (
                "allows"
                if any(r is True for r in results)
                else "inconclusive"
                if None in results
                else "blocks"
            )
            found.append(f"{directive!r}: {verdict}")
            verdicts.append(verdict)
    if not verdicts:
        return "absent", "no frame-ancestors directive"
    overall = "blocks" if "blocks" in verdicts else "inconclusive" if "inconclusive" in verdicts else "allows"
    return overall, "; ".join(found)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _fetch(url: str, timeout: float) -> tuple[int, Any, bytes]:
    """GET without following redirects: (status, headers, first bytes of body)."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": f"shiny-mcp-sandbox-check/{__version__}"})
    try:
        with opener.open(request, timeout=timeout) as r:
            return r.status, r.headers, r.read(65536)
    except HTTPError as e:
        return e.code, e.headers, e.read(4096) if e.fp else b""


def _describe_network_error(e: BaseException) -> str:
    reason = e.reason if isinstance(e, URLError) else e
    if isinstance(reason, ssl.SSLCertVerificationError):
        return f"TLS certificate not accepted ({reason.verify_message}); is the hostname on the certificate?"
    if isinstance(reason, ssl.SSLError):
        return f"TLS handshake failed ({reason})"
    if isinstance(reason, socket.gaierror):
        return f"DNS lookup failed ({reason}); does the sandbox hostname resolve?"
    return f"could not connect ({reason})"


def check_sandbox(sandbox_url: str, host_origin: str, *, timeout: float = 10.0) -> list[SandboxCheck]:
    """Check a deployed sandbox from the server side, as far as HTTP allows.

    Verifies that ``sandbox_url`` is on a different origin than ``host_origin``
    (the Shiny app's origin as browsers see it), answers directly (no redirect,
    no login page) over working TLS, is a shiny-mcp-host sandbox, allows
    ``host_origin`` to embed it, and carries no header that would stop a
    browser from framing it (``X-Frame-Options``, an extra
    ``frame-ancestors``). Passing does **not** prove a browser can embed it
    (cookies, extensions, network paths and browser policy are not visible
    here): confirm with a rendered widget reaching ``data-state="ready"``.
    """
    checks: list[SandboxCheck] = []

    def add(name: str, ok: bool, detail: str) -> bool:
        checks.append(SandboxCheck(name, ok, detail))
        return ok

    parts = urlsplit(sandbox_url)
    host = urlsplit(host_origin)
    sandbox_origin = f"{parts.scheme}://{parts.netloc}".lower()
    host_origin = f"{host.scheme}://{host.netloc}".lower()
    if not add(
        "url",
        parts.scheme in ("http", "https") and bool(parts.netloc) and parts.path.endswith("/sandbox.html"),
        f"{sandbox_url} (expected an http(s) URL ending in /sandbox.html)",
    ):
        return checks
    if not add(
        "distinct origin",
        sandbox_origin != host_origin,
        f"sandbox {sandbox_origin} vs app {host_origin}; they must differ (use a second hostname)",
    ):
        return checks
    if (parts.hostname or "") == (host.hostname or ""):
        add("distinct hostname", False, "only the port differs: cookies are shared across ports")
    if host.scheme == "https":
        add("https", parts.scheme == "https", "an https app can only embed an https sandbox")

    nonce = secrets.token_urlsafe(16)
    page = f"{sandbox_url}?{urlencode({'host': host_origin, 'nonce': nonce})}"
    try:
        status, headers, body = _fetch(page, timeout)
    except (URLError, OSError) as e:
        add("reachable", False, f"{sandbox_url}: {_describe_network_error(e)}")
        return checks
    location = headers.get("Location") if headers else None
    if 300 <= status < 400:
        add(
            "reachable",
            False,
            f"HTTP {status} redirect to {location!r}. The sandbox hostname must serve the page itself; "
            "a redirect (to the main hostname or a login page) defeats the separate origin.",
        )
        return checks
    text = body.decode("utf-8", "replace")
    if status == 403 and "Embedding origin not allowed" in text:
        add(
            "reachable",
            False,
            f"HTTP 403 from the sandbox: {host_origin} is not in its allowed_host_origins "
            "(SHINY_APP_ORIGIN for the Connect example).",
        )
        return checks
    if status in (401, 403):
        add(
            "reachable",
            False,
            f"HTTP {status}: the sandbox requires authentication. Browsers carry no login to the "
            "sandbox hostname; give it anonymous access (it holds no data).",
        )
        return checks
    if not add("reachable", status == 200, f"HTTP {status} from {sandbox_url}"):
        return checks
    add(
        "is sandbox page",
        '<script src="sandbox.js"></script>' in text,
        "response is the shiny-mcp-host sandbox page" if "sandbox.js" in text else "unexpected body",
    )

    policies = headers.get_all("Content-Security-Policy") or []
    verdict, found = frame_ancestors_verdict(policies, host_origin, sandbox_origin)
    explain = {
        "allows": f"every policy lets {host_origin} embed the sandbox",
        "blocks": f"a policy does not allow {host_origin}; browsers will refuse to embed the sandbox "
        "(an extra policy, e.g. from a proxy, blocks even if the sandbox's own allows)",
        "inconclusive": "a source expression this check does not model; confirm in a browser",
        "absent": "the sandbox's own frame-ancestors header is missing (stripped by a proxy?)",
    }[verdict]
    checks.append(
        SandboxCheck(
            "frame-ancestors",
            verdict == "allows",
            f"{explain}. Directives: {found}",
            inconclusive=verdict == "inconclusive",
        )
    )
    add(
        "restrictive CSP",
        any("default-src 'none'" in p and "connect-src 'none'" in p for p in policies),
        "default-src 'none' and connect-src 'none' by default" if policies else "no CSP header",
    )
    xfo = headers.get("X-Frame-Options")
    add("no X-Frame-Options", xfo is None, f"X-Frame-Options: {xfo}" if xfo else "absent")

    try:
        status, headers, _ = _fetch(urljoin(sandbox_url, "sandbox.js"), timeout)
        ctype = headers.get("Content-Type", "") if headers else ""
        add(
            "sandbox.js",
            status == 200 and "javascript" in ctype,
            f"HTTP {status}, {ctype or 'no content type'}",
        )
    except (URLError, OSError) as e:
        add("sandbox.js", False, _describe_network_error(e))
    try:
        status, _, body = _fetch(urljoin(sandbox_url, "healthz"), timeout)
        data = json.loads(body) if status == 200 else {}
        version = data.get("version") if isinstance(data, dict) else None
        add(
            "version",
            isinstance(data, dict) and data.get("service") == SERVICE_NAME,
            f"shiny-mcp-host sandbox {version}" if version else f"HTTP {status} from healthz",
        )
    except (URLError, OSError, ValueError) as e:
        add("version", False, str(e))
    return checks


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

    parser = argparse.ArgumentParser(
        description="Serve the shiny-mcp-host sandbox proxy, or --check a deployed one."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allow-host-origin",
        action="append",
        dest="origins",
        help="Shiny app origin allowed to embed the sandbox (repeatable; supports '*' "
        f"port wildcards). Default: {' '.join(DEFAULT_HOST_ORIGINS)}",
    )
    parser.add_argument(
        "--check",
        metavar="SANDBOX_URL",
        help="Instead of serving, check a deployed sandbox (.../sandbox.html) from the server side: "
        "reachability, redirects, TLS and embedding headers. Needs --host-origin.",
    )
    parser.add_argument(
        "--host-origin", help="With --check: the Shiny app's origin, e.g. https://app.example.com"
    )
    args = parser.parse_args(argv)
    if args.check:
        if not args.host_origin:
            parser.error("--check needs --host-origin")
        checks = check_sandbox(args.check, args.host_origin)
        for c in checks:
            print(f"{c.label}  {c.name}: {c.detail}")
        ok = all(c.ok for c in checks)
        print(
            "\nServer-side checks passed. This does not prove browser embedding: confirm that a "
            'rendered widget reaches data-state="ready".'
            if ok
            else "\nServer-side checks did not all pass (INCONCLUSIVE counts as not passed); see "
            "docs/posit-connect-validation.md#7-failure-diagnosis."
        )
        raise SystemExit(0 if ok else 1)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(sandbox_app(args.origins or DEFAULT_HOST_ORIGINS), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
