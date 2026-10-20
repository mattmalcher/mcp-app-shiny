import json

import pytest
from starlette.testclient import TestClient

from shiny_mcp_host.sandbox import build_csp_header, sandbox_app

NONCE = "a" * 32


@pytest.fixture
def client():
    return TestClient(sandbox_app(["http://127.0.0.1:*", "https://app.example.com"]), base_url="http://localhost:8765")


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
    r = client.get("/sandbox.html", params={"host": "https://app.example.com", "nonce": NONCE, "csp": json.dumps(csp)})
    header = r.headers["content-security-policy"]
    assert "connect-src https://api.example.com" in header
    assert "script-src 'self' 'unsafe-inline' https://cdn.example.com" in header
    assert "evil" not in header


@pytest.mark.parametrize(
    "host",
    ["http://evil.example.com", "https://app.example.com.evil.com", "javascript:alert(1)", "", "http://127.0.0.1:8000/path"],
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
