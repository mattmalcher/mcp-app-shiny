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
