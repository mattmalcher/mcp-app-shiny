"""A15: the same unmodified official server behaves the same in upstream basic-host.

Requires a built checkout of modelcontextprotocol/ext-apps (``make setup-compat``),
located via EXT_APPS_DIR. The Shiny side of the comparison is
test_official_demo.py::test_standalone_render_and_view_tool_call.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import procs

pytestmark = [pytest.mark.e2e, pytest.mark.compat]

EXT_APPS_DIR = Path(os.environ.get("EXT_APPS_DIR", procs.ROOT / "build/ext-apps"))
BASIC_HOST = EXT_APPS_DIR / "examples/basic-host"


def test_basic_host_baseline(demo_server):
    if not (BASIC_HOST / "dist/index.html").exists():
        message = f"upstream basic-host not built at {BASIC_HOST} (run `make setup-compat`)"
        if os.environ.get("REQUIRE_COMPAT"):
            pytest.fail(message)
        pytest.skip(message)
    import sys

    sys.path.insert(0, str(procs.ROOT / "scripts"))
    from basic_host_baseline import main  # type: ignore[import-not-found]

    host_port = procs.free_port()
    bun = EXT_APPS_DIR / "node_modules/.bin/bun"
    # basic-host hardcodes its sandbox at localhost:8081.
    proc = procs.Proc(
        "basic-host",
        [str(bun), "serve.ts"],
        host_port,
        env={"HOST_PORT": str(host_port), "SANDBOX_PORT": "8081", "SERVERS": f'["{demo_server.url}/mcp"]'},
        cwd=BASIC_HOST,
    )
    try:
        assert main(f"http://localhost:{host_port}") == 0
    finally:
        proc.stop()
