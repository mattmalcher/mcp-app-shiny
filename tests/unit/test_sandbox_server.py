import json

import pytest
from starlette.testclient import TestClient

from shiny_mcp_host.sandbox import build_csp_header, sandbox_app

pytestmark = pytest.mark.security

NONCE = "a" * 32


@pytest.fixture
def client():
    return TestClient(
        sandbox_app(["http://127.0.0.1:*", "https://app.example.com"]), base_url="http://localhost:8765"
    )


def test_default_csp_is_restrictive(client):
    r = client.get("/sandbox.html", params={"host": "http://127.0.0.1:8000", "nonce": NONCE})
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    for directive in (
        "default-src 'none'",
        "connect-src 'none'",
        "frame-src 'none'",
        "object-src 'none'",
        "frame-ancestors http://127.0.0.1:8000",
    ):
        assert directive in csp
    assert "unsafe-eval" not in csp
    assert r.headers["cache-control"] == "no-store"
    assert '<script src="sandbox.js">' in r.text


def test_declared_csp_is_revalidated(client):
    csp = {
        "connectDomains": ["https://api.example.com", "https://evil; script-src *"],
        "resourceDomains": ["https://cdn.example.com"],
    }
    r = client.get(
        "/sandbox.html", params={"host": "https://app.example.com", "nonce": NONCE, "csp": json.dumps(csp)}
    )
    header = r.headers["content-security-policy"]
    assert "connect-src https://api.example.com" in header
    assert "script-src 'self' 'unsafe-inline' https://cdn.example.com" in header
    assert "evil" not in header


@pytest.mark.parametrize(
    "host",
    [
        "http://evil.example.com",
        "https://app.example.com.evil.com",
        "javascript:alert(1)",
        "",
        "http://127.0.0.1:8000/path",
    ],
)
def test_disallowed_host_origin(client, host):
    r = client.get("/sandbox.html", params={"host": host, "nonce": NONCE})
    assert r.status_code == 403


def test_same_origin_refused():
    c = TestClient(sandbox_app(["http://localhost:*"]), base_url="http://localhost:8765")
    r = c.get("/sandbox.html", params={"host": "http://localhost:8765", "nonce": NONCE})
    assert r.status_code == 403


@pytest.mark.parametrize("nonce", ["", "short", "x" * 300, "bad nonce!!!!!!!!!!!!!"])
def test_bad_nonce(client, nonce):
    r = client.get("/sandbox.html", params={"host": "http://127.0.0.1:8000", "nonce": nonce})
    assert r.status_code == 400


def test_invalid_csp_param(client):
    r = client.get("/sandbox.html", params={"host": "http://127.0.0.1:8000", "nonce": NONCE, "csp": "{"})
    assert r.status_code == 400


def test_only_sandbox_files_served(client):
    assert client.get("/").status_code == 404
    assert client.get("/etc/passwd").status_code == 404
    js = client.get("/sandbox.js")
    assert js.status_code == 200 and js.headers["content-type"].startswith("text/javascript")


def test_build_csp_header_base_uri_default():
    assert "base-uri 'self'" in build_csp_header(None, "http://127.0.0.1:8000")


def test_healthz_identifies_the_service(client):
    r = client.get("/healthz")
    assert r.json()["service"] == "shiny-mcp-host-sandbox"


def test_start_refuses_a_port_held_by_something_else():
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from shiny_mcp_host.sandbox import start_sandbox_server

    class Other(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello, I am not a sandbox")

        def log_message(self, *a):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = HTTPServer(("127.0.0.1", port), Other)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(RuntimeError, match="not by a shiny-mcp-host sandbox"):
            start_sandbox_server(port=port)
    finally:
        srv.shutdown()


def test_start_reuses_a_verified_sandbox():
    import socket

    from shiny_mcp_host import sandbox as sb

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    first = sb.start_sandbox_server(port=port)
    try:
        assert sb.probe_sandbox("127.0.0.1", port) is not None
        sb._RUNNING.pop(port)  # simulate another process owning it
        reused = sb.start_sandbox_server(port=port)
        assert reused.url == f"http://localhost:{port}/sandbox.html"
    finally:
        first.stop()


@pytest.mark.parametrize("mode", ["mounted", "root_path"])
def test_works_under_a_path_prefix(mode):
    """Hosts like Posit Connect serve content under /content/<guid>/."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    inner = sandbox_app(["https://connect.example.com"])
    if mode == "mounted":
        app, kw = Starlette(routes=[Mount("/content/abc", inner)]), {}
    else:
        app, kw = inner, {"root_path": "/content/abc"}
    c = TestClient(app, base_url="https://connect-sandbox.example.com", **kw)
    r = c.get("/content/abc/sandbox.html", params={"host": "https://connect.example.com", "nonce": NONCE})
    assert r.status_code == 200
    # The script is referenced relatively, so it resolves under the same prefix.
    assert '<script src="sandbox.js">' in r.text
    assert c.get("/content/abc/sandbox.js").status_code == 200
    assert "frame-ancestors https://connect.example.com" in r.headers["content-security-policy"]
