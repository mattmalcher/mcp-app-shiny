from __future__ import annotations

import pytest

from tests import procs


@pytest.fixture(scope="session")
def fixture_server():
    p = procs.fixture_server(procs.free_port())
    yield p
    p.stop()


@pytest.fixture(scope="session")
def demo_server():
    p = procs.demo_server(procs.free_port())
    yield p
    p.stop()


@pytest.fixture
def serve():
    """Serve an ASGI app on a free port in a thread; returns its base URL."""
    import socket
    import threading
    import time

    import uvicorn

    servers = []

    def start(app) -> str:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning", lifespan="off"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.02)
        servers.append((server, thread))
        return f"http://localhost:{port}"

    yield start
    for server, thread in servers:
        server.should_exit = True
        thread.join(timeout=5)
