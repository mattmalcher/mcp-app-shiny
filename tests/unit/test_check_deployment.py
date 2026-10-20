"""scripts/check_deployment.py: reporting is honest, and evidence survives failures."""

from __future__ import annotations

import json
import socket
import sys

import pytest

from shiny_mcp_host.sandbox import sandbox_app
from tests import procs

sys.path.insert(0, str(procs.ROOT / "scripts"))
import check_deployment as cd  # type: ignore[import-not-found]  # noqa: E402

APP = "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    ("status", "location", "error", "verdict"),
    [
        (401, None, None, "rejected"),
        (403, None, None, "rejected"),
        (302, "https://connect.example.com/__login__?url=x", None, "rejected"),
        (200, None, None, "served"),
        (202, None, None, "served"),
        # None of these say anything about authentication.
        (500, None, None, "inconclusive"),
        (502, None, None, "inconclusive"),
        (404, None, None, "inconclusive"),
        (421, None, None, "inconclusive"),
        (302, "https://connect.example.com/other", None, "inconclusive"),
        (None, None, "TimeoutError: timed out", "inconclusive"),
        (None, None, "URLError: [Errno 111] Connection refused", "inconclusive"),
    ],
)
def test_classify_unauthenticated(status, location, error, verdict):
    assert cd.classify_unauthenticated(status, location, error)[0] == verdict


def _status_app(status: int, headers=()):
    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": status, "headers": list(headers)})
        await send({"type": "http.response.body", "body": b"{}"})

    return app


@pytest.mark.parametrize(
    ("app", "verdict"),
    [
        (_status_app(401), "rejected"),
        (_status_app(302, [(b"location", b"/__login__?u=1")]), "rejected"),
        (_status_app(200), "served"),
        (_status_app(500), "inconclusive"),
    ],
)
def test_unauthenticated_probe_against_a_server(serve, app, verdict):
    base = serve(app)
    assert cd.classify_unauthenticated(*cd.post_without_credentials(f"{base}/mcp"))[0] == verdict


def test_unauthenticated_probe_when_nothing_listens():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    status, location, error = cd.post_without_credentials(f"http://127.0.0.1:{port}/mcp")
    assert status is None and error
    assert cd.classify_unauthenticated(status, location, error)[0] == "inconclusive"


def test_evidence_is_written_when_the_browser_stage_crashes(serve, tmp_path, monkeypatch):
    base = serve(sandbox_app([APP]))

    def crash(*a, **k):
        raise RuntimeError("Executable doesn't exist at /nowhere/chromium")

    monkeypatch.setattr(cd, "check_browser", crash)
    argv = ["check_deployment.py", "--app-url", f"{APP}/", "--sandbox-url", f"{base}/sandbox.html"]
    monkeypatch.setattr(sys, "argv", [*argv, "--evidence-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as exit_:
        cd.main()
    assert exit_.value.code == 1
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    assert evidence["passed"] is False and "finished" in evidence
    crashed = [c for c in evidence["checks"] if c["name"] == "unexpected error"]
    assert crashed and crashed[0]["stage"] == "browser" and "Executable" in crashed[0]["detail"]
    assert "RuntimeError" in evidence["errors"][0]["traceback"]
    # The server-side results from before the crash are kept.
    assert any(c["name"] == "sandbox reachable" and c["ok"] for c in evidence["checks"])


def test_evidence_is_written_when_interrupted(serve, tmp_path, monkeypatch):
    base = serve(sandbox_app([APP]))

    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cd, "check_browser", interrupt)
    argv = ["check_deployment.py", "--app-url", f"{APP}/", "--sandbox-url", f"{base}/sandbox.html"]
    monkeypatch.setattr(sys, "argv", [*argv, "--evidence-dir", str(tmp_path)])
    with pytest.raises(KeyboardInterrupt):
        cd.main()
    evidence = json.loads((tmp_path / "evidence.json").read_text())
    # The server checks passed, but an interrupted run is never reported as passed.
    assert evidence["completed"] is False and evidence["passed"] is False
    assert any(c["name"] == "sandbox reachable" and c["ok"] for c in evidence["checks"])


def test_inconclusive_checks_do_not_pass():
    report = cd.Report()
    report.add("server", "a", True, "")
    report.add("server", "b", False, "", inconclusive=True)
    assert not report.ok


@pytest.mark.parametrize(
    ("response", "ok", "inconclusive"),
    [
        ({"status": 200, "headers": {}}, True, False),
        ({"status": 200, "headers": {"content-security-policy": ["script-src 'self'"]}}, False, True),
        ({"status": 302, "headers": {"location": ["https://x/__login__?…"]}}, False, True),
        ({"error": "timed out"}, False, True),
    ],
)
def test_trusted_mode_reports_the_app_page_csp_views_inherit(response, ok, inconclusive):
    report = cd.Report()
    cd.check_app_page_csp(report, response)
    (check,) = report.checks
    assert (check.ok, check.inconclusive) == (ok, inconclusive)
