"""The Posit Connect deployment folders (scripts/package_connect.py) and the sandbox entry point.

`make rehearse-connect` runs the folders end to end from clean venvs; these
are the fast structural checks.
"""

from __future__ import annotations

import runpy
import sys

import pytest
from starlette.testclient import TestClient

from tests import procs

SANDBOX_APP = procs.ROOT / "examples/posit_connect/sandbox/app.py"


def test_bundle_layout_and_requirements(tmp_path):
    sys.path.insert(0, str(procs.ROOT / "scripts"))
    from package_connect import package  # type: ignore[import-not-found]

    out = package(tmp_path / "connect", from_git="v-test")
    assert {p.name for p in out.iterdir()} == {"mcp-server", "sandbox", "shiny-app", "REVISION"}
    assert (out / "mcp-server/server.py").exists() and (out / "mcp-server/view.built.html").exists()
    example = procs.ROOT / "examples/sales_explorer/app.py"
    assert (out / "shiny-app/app.py").read_text() == example.read_text()
    assert (out / "sandbox/app.py").read_text() == SANDBOX_APP.read_text()
    for name in ("sandbox", "shiny-app"):
        reqs = (out / name / "requirements.txt").read_text().splitlines()
        assert reqs[0] == "shiny-mcp-host @ git+https://github.com/mattmalcher/mcp-app-shiny@v-test"
        pinned = {r.split("==")[0] for r in reqs[1:]}
        assert {"shiny", "shinychat", "mcp", "starlette", "uvicorn"} <= pinned
        assert all("==" in r for r in reqs[1:])


def test_sandbox_entry_point_requires_the_app_origin(monkeypatch):
    monkeypatch.delenv("SHINY_APP_ORIGIN", raising=False)
    with pytest.raises(RuntimeError, match="SHINY_APP_ORIGIN"):
        runpy.run_path(str(SANDBOX_APP))


def test_sandbox_entry_point_allows_only_the_configured_origin(monkeypatch):
    monkeypatch.setenv("SHINY_APP_ORIGIN", "https://connect.example.com/, https://other.example.com")
    app = runpy.run_path(str(SANDBOX_APP))["app"]
    c = TestClient(app, base_url="https://connect-sandbox.example.com")
    nonce = "n" * 32
    ok = c.get("/sandbox.html", params={"host": "https://connect.example.com", "nonce": nonce})
    assert ok.status_code == 200
    assert "frame-ancestors https://connect.example.com" in ok.headers["content-security-policy"]
    other = c.get("/sandbox.html", params={"host": "https://other.example.com", "nonce": nonce})
    assert other.status_code == 200
    denied = c.get("/sandbox.html", params={"host": "http://127.0.0.1:8000", "nonce": nonce})
    assert denied.status_code == 403
