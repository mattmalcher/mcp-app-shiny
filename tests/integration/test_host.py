"""MCPHost against real MCP servers, with the browser side simulated.

The fake session records every Python -> browser custom message and lets the
test invoke the registered message handler exactly as ``makeRequest`` would.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shiny_mcp_host import HostPolicy, MCPHost, SandboxConfigError

pytestmark = pytest.mark.integration

SANDBOX = "http://localhost:8765/sandbox.html"


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.handlers: dict[str, Any] = {}
        self.ended: list[Any] = []
        self.http_conn = None
        self._event = asyncio.Event()

    def ns(self, id: str) -> str:
        return id

    def root_scope(self) -> FakeSession:
        return self

    def set_message_handler(self, name: str, fn: Any) -> str:
        if fn is None:
            self.handlers.pop(name, None)
        else:
            self.handlers[name] = fn
        return name

    async def send_custom_message(self, type: str, message: dict[str, Any]) -> None:
        assert type == "shiny-mcp-host"
        self.sent.append(message)
        self._event.set()

    def on_ended(self, fn: Any) -> None:
        self.ended.append(fn)

    async def request(self, host: MCPHost, **envelope: Any) -> dict[str, Any]:
        handler = self.handlers[f"shiny-mcp-host:{host.id}"]
        return await handler({"v": 1, "payload": {}, **envelope})

    async def wait_for(self, pred, timeout: float = 15) -> dict[str, Any]:
        async def loop():
            while True:
                for m in self.sent:
                    if pred(m):
                        return m
                self._event.clear()
                await self._event.wait()

        return await asyncio.wait_for(loop(), timeout)

    def of(self, kind: str, widget: str | None = None) -> list[dict[str, Any]]:
        return [m for m in self.sent if m["kind"] == kind and (widget is None or m["widget_id"] == widget)]


@pytest.fixture
async def env(fixture_server):
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX)  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    await host.call_tool("fx", "reset")
    yield host, session
    await host.close()


async def mount(host: MCPHost, session: FakeSession, widget: str, mount_id: str = "m1") -> None:
    ack = await session.request(host, kind="mount", widget_id=widget, mount_id=mount_id)
    assert ack["ok"] is True


async def render(host, session, widget="w1", tool="counter", args=None, mount_id="m1"):
    await mount(host, session, widget, mount_id)
    result = await host.call_and_render("fx", tool, args or {"start": 3}, target=widget)
    msg = await session.wait_for(
        lambda m: m["kind"] in ("render", "error", "fallback") and m["widget_id"] == widget
    )
    return result, msg


async def view_request(host, session, msg, kind, params, request_id, widget="w1", mount_id="m1"):
    ack = await session.request(
        host,
        kind=kind,
        widget_id=widget,
        mount_id=mount_id,
        request_id=request_id,
        render_id=msg["payload"]["render_id"],
        payload={"params": params},
    )
    if ack["status"] == "done":
        return ack
    resp = await session.wait_for(lambda m: m["kind"] == "response" and m.get("request_id") == request_id)
    assert resp["mount_id"] == mount_id and resp["widget_id"] == widget
    return resp["payload"]


async def stats(host) -> dict[str, int]:
    r = await host.call_tool("fx", "stats")
    return (
        r.structured_content["result"] if "result" in (r.structured_content or {}) else r.structured_content
    )


# --------------------------------------------------------------------- A01-A03


async def test_a01_discovers_ui_tools(env):
    host, _ = env
    tools = {t.name: t for t in await host.list_tools("fx")}
    assert tools["counter"].has_ui and tools["counter"].ui_resource_uri == "ui://fixture/counter.html"
    assert tools["increment"].visibility == ("app",)
    assert tools["model-only"].visibility == ("model",)
    assert not tools["plain"].has_ui


async def test_a01_official_demo_server(demo_server):
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX)  # type: ignore[arg-type]
    host.add_server("demo", url=f"{demo_server.url}/mcp")
    try:
        tools = {t.name: t for t in await host.list_tools("demo")}
        assert tools["get-time"].ui_resource_uri == "ui://get-time/mcp-app.html"
        await mount(host, session, "w")
        result = await host.call_and_render("demo", "get-time", {}, target="w")
        assert result.display == "view" and "time" in result.result.structured_content
        msg = await session.wait_for(lambda m: m["kind"] == "render")
        assert "Get Server Time" in msg["payload"]["resource"]["html"]
    finally:
        await host.close()


async def test_a02_tool_results_and_errors(env):
    host, session = env
    r = await host.call_tool("fx", "plain", {"text": "x"})
    assert r.content[0].text == "plain: x" and not r.is_error
    failed = await host.call_tool("fx", "fail")
    assert failed.is_error
    # A non-UI tool renders its text fallback.
    await mount(host, session, "w1")
    out = await host.call_and_render("fx", "fail", {}, target="w1")
    fb = await session.wait_for(lambda m: m["kind"] == "fallback")
    assert fb["payload"]["is_error"] is True
    # A tool-level error (isError) is a tool failure, not a render failure.
    assert out.display == "text" and out.tool_failed and out.tool_error is None and out.render_error is None


async def test_a03_render_message(env):
    host, session = env
    result, msg = await render(host, session)
    assert result.display == "view" and not result.tool_failed
    assert result.result.structured_content == {"count": 3}
    p = msg["payload"]
    assert msg["mount_id"] == "m1"
    assert p["resource"]["uri"] == "ui://fixture/counter.html"
    assert p["resource"]["html"].startswith("<!doctype html>")
    assert p["tool_input"] == {"arguments": {"start": 3}}
    assert p["tool_result"]["structuredContent"] == {"count": 3}
    assert "resultType" not in p["tool_result"]
    assert p["sandbox"] == {"url": SANDBOX, "view_same_origin": False}
    assert p["host_context"]["toolInfo"]["tool"]["name"] == "counter"


async def test_render_before_mount_is_delivered_on_mount(env):
    host, session = env
    await host.call_and_render("fx", "counter", {"start": 1}, target="late")
    assert session.of("render", "late") == []
    await mount(host, session, "late", "m9")
    msg = await session.wait_for(lambda m: m["kind"] == "render" and m["widget_id"] == "late")
    assert msg["mount_id"] == "m9"
    # Remount (e.g. element moved / re-rendered) re-delivers to the new mount.
    await mount(host, session, "late", "m10")
    await session.wait_for(lambda m: m["kind"] == "render" and m.get("mount_id") == "m10")


async def test_csp_is_policy_filtered(fixture_server):
    session = FakeSession()
    policy = HostPolicy(allowed_csp_domains=["https://api.example.com"])
    host = MCPHost(session, sandbox_url=SANDBOX, policy=policy)  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    try:
        _, msg = await render(host, session, tool="csp-demo", args={})
        assert msg["payload"]["resource"]["csp"] == {"connectDomains": ["https://api.example.com"]}
    finally:
        await host.close()


# ------------------------------------------------------------ A06 / security


async def test_a06_view_tool_call_round_trip(env):
    host, session = env
    _, msg = await render(host, session)
    out = await view_request(
        host, session, msg, "tools/call", {"name": "increment", "arguments": {"count": 3}}, "m1.1"
    )
    assert out["ok"] is True and out["result"]["structuredContent"] == {"count": 4}


@pytest.mark.security
@pytest.mark.parametrize("tool", ["secret-admin", "model-only", "does-not-exist"])
async def test_unauthorised_tool_rejected_with_zero_invocations(env, tool):
    host, session = env
    _, msg = await render(host, session)
    out = await view_request(host, session, msg, "tools/call", {"name": tool, "arguments": {}}, "m1.2")
    assert out["ok"] is False and out["error"]["code"] == "policy_rejected"
    counts = await stats(host)
    assert counts.get("secret-admin", 0) == 0 and counts.get("model-only", 0) == 0


@pytest.mark.security
async def test_allowlist_policy(fixture_server):
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX, policy=HostPolicy(app_tools={"fx": ["counter"]}))  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    try:
        _, msg = await render(host, session)
        out = await view_request(
            host, session, msg, "tools/call", {"name": "increment", "arguments": {"count": 1}}, "m1.1"
        )
        assert out["ok"] is False and "allowlist" in out["error"]["message"]
    finally:
        await host.close()


@pytest.mark.security
async def test_stale_mount_and_render_rejected(env):
    host, session = env
    _, msg = await render(host, session)
    params = {"name": "increment", "arguments": {"count": 1}}
    stale_mount = await session.request(
        host,
        kind="tools/call",
        widget_id="w1",
        mount_id="other",
        request_id="x.1",
        render_id=1,
        payload={"params": params},
    )
    assert stale_mount["ok"] is False
    stale_render = await session.request(
        host,
        kind="tools/call",
        widget_id="w1",
        mount_id="m1",
        request_id="x.2",
        render_id=99,
        payload={"params": params},
    )
    assert stale_render["ok"] is False
    unknown = await session.request(
        host,
        kind="tools/call",
        widget_id="nope",
        mount_id="m1",
        request_id="x.3",
        render_id=1,
        payload={"params": params},
    )
    assert unknown["ok"] is False
    assert (await stats(host))["increment"] == 0


@pytest.mark.security
async def test_malformed_envelopes_rejected(env):
    host, session = env
    handler = session.handlers[f"shiny-mcp-host:{host.id}"]
    for raw in [None, "x", {"v": 1}, {"v": 1, "kind": "exec", "widget_id": "w", "mount_id": "m"}]:
        out = await handler(raw)
        assert out["ok"] is False


@pytest.mark.security
async def test_oversized_tool_arguments_rejected(fixture_server):
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX, policy=HostPolicy(max_tool_payload_bytes=1000))  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    try:
        _, msg = await render(host, session)
        out = await view_request(
            host,
            session,
            msg,
            "tools/call",
            {"name": "increment", "arguments": {"count": 1, "pad": "x" * 5000}},
            "m1.1",
        )
        assert out["ok"] is False and "size" in out["error"]["message"]
    finally:
        await host.close()


@pytest.mark.security
async def test_open_link_validation(env):
    host, session = env
    _, msg = await render(host, session)
    ok = await view_request(host, session, msg, "open-link", {"url": "https://example.com/"}, "m1.1")
    assert ok["result"] == {"url": "https://example.com/"}
    for i, url in enumerate(["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x"]):
        bad = await view_request(host, session, msg, "open-link", {"url": url}, f"m1.{i + 2}")
        assert bad["ok"] is False and bad["error"]["code"] == "policy_rejected"


async def test_pending_request_limit(fixture_server):
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX, policy=HostPolicy(max_pending_per_widget=2))  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    try:
        _, msg = await render(host, session)
        for i in range(2):
            ack = await session.request(
                host,
                kind="resources/read",
                widget_id="w1",
                mount_id="m1",
                request_id=f"m1.{i}",
                render_id=1,
                payload={"params": {"uri": "ui://fixture/counter.html"}},
            )
            assert ack["status"] == "pending"
        third = await session.request(
            host,
            kind="resources/read",
            widget_id="w1",
            mount_id="m1",
            request_id="m1.9",
            render_id=1,
            payload={"params": {"uri": "ui://fixture/counter.html"}},
        )
        assert third["ok"] is False and "Too many" in third["error"]["message"]
    finally:
        await host.close()


# ----------------------------------------------------------------- A09 / A10


async def test_a09_model_context_isolated_by_widget(env):
    host, session = env
    events = []
    host.on_model_context(events.append)
    _, m1 = await render(host, session, widget="a", mount_id="ma")
    _, m2 = await render(host, session, widget="b", mount_id="mb")
    await view_request(
        host,
        session,
        m1,
        "model-context",
        {"structuredContent": {"count": 1}},
        "ma.1",
        widget="a",
        mount_id="ma",
    )
    await view_request(
        host,
        session,
        m2,
        "model-context",
        {"content": [{"type": "text", "text": "b"}]},
        "mb.1",
        widget="b",
        mount_id="mb",
    )
    assert [(e.widget_id, e.structured_content, e.content) for e in events] == [
        ("a", {"count": 1}, None),
        ("b", None, [{"type": "text", "text": "b"}]),
    ]
    from shiny import reactive

    with reactive.isolate():
        assert host.model_context("a").structured_content == {"count": 1}
        assert host.model_context("b").content[0]["text"] == "b"
    await view_request(host, session, m1, "model-context", {"content": []}, "ma.2", widget="a", mount_id="ma")
    with reactive.isolate():
        assert host.model_context("a") is None and host.model_context("b") is not None


async def test_app_message_callback(env):
    host, session = env
    _, msg = await render(host, session)
    params = {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    rejected = await view_request(host, session, msg, "message", params, "m1.1")
    assert rejected["result"] == {"isError": True}  # no callback registered
    got = []
    host.on_message(got.append)
    accepted = await view_request(host, session, msg, "message", params, "m1.2")
    assert accepted["result"] == {} and got[0].text == "hello" and got[0].widget_id == "w1"


async def test_a10_parallel_widgets_same_server(env):
    host, session = env
    widgets = [f"p{i}" for i in range(4)]
    renders = {}
    for w in widgets:
        _, renders[w] = await render(host, session, widget=w, mount_id=f"{w}m")

    async def one(w: str, i: int, n: int):
        return (
            w,
            n,
            await view_request(
                host,
                session,
                renders[w],
                "tools/call",
                {"name": "increment", "arguments": {"count": n}},
                f"{w}m.{i}",
                widget=w,
                mount_id=f"{w}m",
            ),
        )

    jobs = [one(w, i, 100 * k + i) for k, w in enumerate(widgets) for i in range(5)]
    for w, n, out in await asyncio.gather(*jobs):
        assert out["ok"], out
        assert out["result"]["structuredContent"] == {"count": n + 1}, (w, n, out)
    assert (await stats(host))["increment"] == 20


# ------------------------------------------------------------------- A11


@pytest.mark.parametrize(
    ("tool", "needle"),
    [
        ("broken-mime", "MIME"),
        ("empty-ui", "empty"),
        ("huge-ui", "exceeds"),
        ("missing-ui", "Unknown resource"),
        ("nonexistent-tool", "has no tool"),
    ],
)
async def test_a11_bad_resources_are_bounded_errors(env, tool, needle):
    host, session = env
    result, msg = await render(host, session, tool=tool, args={})
    assert result.display == "error" and needle in result.render_error
    assert msg["kind"] == "error" and needle in msg["payload"]["message"]
    if tool not in ("missing-ui", "nonexistent-tool"):
        assert "text fallback" in msg["payload"].get("fallback_text", "")


async def test_a11_server_unavailable():
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX, policy=HostPolicy(request_timeout=5))  # type: ignore[arg-type]
    host.add_server("down", url="http://127.0.0.1:9/mcp")
    try:
        await mount(host, session, "w1")
        result = await host.call_and_render("down", "counter", {}, target="w1")
        assert result.display == "error" and "Could not connect" in result.render_error
        err = await session.wait_for(lambda m: m["kind"] == "error")
        assert err["payload"]["code"] == "server_unavailable"
    finally:
        await host.close()


async def test_a11_timeout_settles_pending(fixture_server):
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX, policy=HostPolicy(request_timeout=1.0))  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    try:
        result, msg = await render(host, session, tool="slow-ui", args={"seconds": 5})
        # The tool timed out but the View still renders, receiving a cancellation.
        assert msg["kind"] == "render" and "timed out" in msg["payload"]["tool_cancelled"]["reason"]
        assert msg["payload"]["tool_result"] is None
        assert result.display == "view" and result.tool_failed and "timed out" in result.tool_error
        assert result.render_error is None
        # The host keeps working afterwards.
        assert (await host.call_tool("fx", "plain")).content[0].text == "plain: hello"
    finally:
        await host.close()


# ------------------------------------------------------------------- A12


async def test_a12_close_cancels_pending_and_closes_transports(fixture_server):
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX, policy=HostPolicy(app_tools={"fx": ["slow"]}))  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    _, msg = await render(host, session)
    ack = await session.request(
        host,
        kind="tools/call",
        widget_id="w1",
        mount_id="m1",
        request_id="m1.1",
        render_id=1,
        payload={"params": {"name": "slow", "arguments": {"seconds": 30}}},
    )
    assert ack["status"] == "pending"
    await asyncio.sleep(0.3)
    assert len(session.ended) == 1  # registered with session.on_ended
    await asyncio.wait_for(session.ended[0](), 10)
    resp = await session.wait_for(lambda m: m["kind"] == "response" and m["request_id"] == "m1.1")
    assert resp["payload"]["error"]["code"] == "cancelled"
    assert not host._servers["fx"].connected
    assert f"shiny-mcp-host:{host.id}" not in session.handlers


async def test_unmount_cancels_widget_requests(env):
    host, session = env
    host.policy = HostPolicy(app_tools={"fx": ["slow"]})
    _, msg = await render(host, session)
    await session.request(
        host,
        kind="tools/call",
        widget_id="w1",
        mount_id="m1",
        request_id="m1.1",
        render_id=1,
        payload={"params": {"name": "slow", "arguments": {"seconds": 30}}},
    )
    await session.request(host, kind="unmount", widget_id="w1", mount_id="m1")
    resp = await session.wait_for(lambda m: m["kind"] == "response" and m["request_id"] == "m1.1")
    assert resp["payload"]["error"]["code"] == "cancelled"


# ------------------------------------------------------- sessions / config


@pytest.mark.security
async def test_sessions_with_identical_widget_ids_are_isolated(fixture_server):
    s1, s2 = FakeSession(), FakeSession()
    h1 = MCPHost(s1, sandbox_url=SANDBOX)  # type: ignore[arg-type]
    h2 = MCPHost(s2, sandbox_url=SANDBOX)  # type: ignore[arg-type]
    for h in (h1, h2):
        h.add_server("fx", url=f"{fixture_server.url}/mcp")
    try:
        await render(h1, s1, args={"start": 1})
        await render(h2, s2, args={"start": 2})
        assert all(
            m["payload"].get("tool_result", {}).get("structuredContent") == {"count": 1}
            for m in s1.of("render")
        )
        assert all(
            m["payload"].get("tool_result", {}).get("structuredContent") == {"count": 2}
            for m in s2.of("render")
        )
        # A mount id from session 1 is meaningless in session 2.
        out = await s2.request(
            h2, kind="tools/call", widget_id="w1", mount_id="nope", request_id="q.1", render_id=1, payload={}
        )
        assert out["ok"] is False
    finally:
        await h1.close()
        await h2.close()


def test_sandbox_required(monkeypatch):
    monkeypatch.delenv("SHINY_MCP_SANDBOX_URL", raising=False)
    with pytest.raises(SandboxConfigError):
        MCPHost(FakeSession())  # type: ignore[arg-type]
    with pytest.raises(SandboxConfigError):
        MCPHost(FakeSession(), sandbox_url="javascript:alert(1)")  # type: ignore[arg-type]


def test_sandbox_same_origin_as_app_refused():
    from types import SimpleNamespace as NS

    session = FakeSession()
    session.http_conn = NS(headers={"host": "127.0.0.1:8000"}, url=NS(scheme="ws"))
    with pytest.raises(SandboxConfigError, match="same as the Shiny app origin"):
        MCPHost(session, sandbox_url="http://127.0.0.1:8000/sandbox.html")  # type: ignore[arg-type]
    MCPHost(session, sandbox_url="http://127.0.0.1:8765/sandbox.html")  # type: ignore[arg-type]


def test_trusted_mode_needs_no_sandbox_and_refuses_ambiguous_config(monkeypatch):
    monkeypatch.setenv("SHINY_MCP_SANDBOX_URL", SANDBOX)
    host = MCPHost(FakeSession(), dangerously_trust_apps=True)  # type: ignore[arg-type]
    assert host.sandbox_url is None
    with pytest.raises(SandboxConfigError, match="drop sandbox_url"):
        MCPHost(FakeSession(), sandbox_url=SANDBOX, dangerously_trust_apps=True)  # type: ignore[arg-type]
    with pytest.raises(SandboxConfigError, match="view_same_origin"):
        MCPHost(FakeSession(), policy=HostPolicy(view_same_origin=True), dangerously_trust_apps=True)  # type: ignore[arg-type]


async def test_trusted_mode_render_message_carries_meta_csp(fixture_server):
    session = FakeSession()
    policy = HostPolicy(allowed_csp_domains=["https://api.example.com"])
    host = MCPHost(session, policy=policy, dangerously_trust_apps=True)  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    try:
        _, msg = await render(host, session, tool="csp-demo", args={})
        sandbox = msg["payload"]["sandbox"]
        assert set(sandbox) == {"trusted", "csp_policy"} and sandbox["trusted"] is True
        assert sandbox["csp_policy"].startswith("default-src 'none'")
        assert "connect-src https://api.example.com" in sandbox["csp_policy"]
        assert "frame-ancestors" not in sandbox["csp_policy"]
    finally:
        await host.close()


async def test_stdio_transport_with_official_server():
    from tests.procs import OFFICIAL_SERVER, require_official_server

    require_official_server()
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX)  # type: ignore[arg-type]
    host.add_server("demo", transport="stdio", command="node", args=[str(OFFICIAL_SERVER), "--stdio"])
    try:
        await mount(host, session, "w")
        result = await host.call_and_render("demo", "get-time", {}, target="w")
        assert result.display == "view" and "time" in result.result.structured_content
        await session.wait_for(lambda m: m["kind"] == "render")
    finally:
        await host.close()
    assert not host._servers["demo"].connected


# ------------------------------------------------------------ render_result


async def test_render_result_does_not_call_the_tool_again(env):
    host, session = env
    await mount(host, session, "w1")
    # An agent loop already called the tool (here: a side-effecting one).
    result = await host.call_tool("fx", "increment", {"count": 1})
    assert (await stats(host))["increment"] == 1
    out = await host.render_result("fx", "increment", {"count": 1}, result, target="w1")
    assert out.display == "view" and not out.tool_failed
    msg = await session.wait_for(lambda m: m["kind"] == "render")
    assert msg["payload"]["tool_result"]["structuredContent"] == {"count": 2}
    assert msg["payload"]["tool_input"] == {"arguments": {"count": 1}}
    assert (await stats(host))["increment"] == 1  # not called again


async def test_render_result_accepts_wire_json_and_errors(env):
    host, session = env
    await mount(host, session, "w1")
    wire = {"content": [{"type": "text", "text": "count=5"}], "structuredContent": {"count": 5}}
    assert (await host.render_result("fx", "counter", {}, wire, target="w1")).display == "view"
    msg = await session.wait_for(lambda m: m["kind"] == "render")
    assert msg["payload"]["tool_result"]["structuredContent"] == {"count": 5}
    # A failed call still renders the View, which receives a cancellation.
    out = await host.render_result("fx", "counter", {}, None, target="w1", error="agent aborted")
    assert out.tool_error == "agent aborted" and out.display == "view" and out.render_error is None
    msg = await session.wait_for(lambda m: m["kind"] == "render" and m["payload"]["render_id"] == 2)
    assert msg["payload"]["tool_cancelled"] == {"reason": "agent aborted"}
    # Malformed results become a readable widget error.
    bad = await host.render_result("fx", "counter", {}, {"content": "nope"}, target="w1")
    assert bad.display == "error" and "Invalid tool result" in bad.render_error
    await session.wait_for(lambda m: m["kind"] == "error")


async def test_render_result_without_ui_shows_text_fallback(env):
    host, session = env
    await mount(host, session, "w1")
    result = await host.call_tool("fx", "plain", {"text": "hi"})
    await host.render_result("fx", "plain", {"text": "hi"}, result, target="w1")
    fb = await session.wait_for(lambda m: m["kind"] == "fallback")
    assert fb["payload"]["text"] == "plain: hi"


async def test_unknown_server_is_a_widget_error(env):
    host, session = env
    await mount(host, session, "w1")
    out = await host.call_and_render("nope", "counter", {}, target="w1")
    assert "Unknown MCP server" in out.render_error
    await session.wait_for(lambda m: m["kind"] == "error")


@pytest.mark.parametrize("slow_tool", ["slow-ui", "slow"])  # View and text-fallback outcomes
async def test_stale_outcomes_never_overwrite_a_newer_render(env, slow_tool):
    host, session = env
    await mount(host, session, "w1")
    older = asyncio.create_task(host.call_and_render("fx", slow_tool, {"seconds": 1.0}, target="w1"))
    await asyncio.sleep(0.2)
    newer = await host.call_and_render("fx", "counter", {"start": 9}, target="w1")
    assert newer.display == "view"
    stale = await older
    assert stale.superseded and stale.display is None and stale.render_error is None
    final = [m for m in session.sent if m["kind"] in ("render", "fallback", "error")]
    assert len(final) == 1 and final[0]["payload"]["tool_result"]["structuredContent"] == {"count": 9}
    # A remount still gets the newest render.
    await mount(host, session, "w1", "m2")
    again = await session.wait_for(lambda m: m["kind"] == "render" and m.get("mount_id") == "m2")
    assert again["payload"]["tool_result"]["structuredContent"] == {"count": 9}


async def test_stale_error_does_not_overwrite(env):
    host, session = env
    await mount(host, session, "w1")
    gate = asyncio.Event()
    conn = host._servers["fx"]
    real_read = conn.read_resource

    async def gated_read(uri, **kw):
        if uri == "ui://fixture/broken-mime.html":
            await gate.wait()  # hold the older call until the newer render is published
        return await real_read(uri, **kw)

    conn.read_resource = gated_read
    older = asyncio.create_task(host.call_and_render("fx", "broken-mime", {}, target="w1"))
    await asyncio.sleep(0.3)
    assert (await host.call_and_render("fx", "counter", {"start": 2}, target="w1")).display == "view"
    gate.set()
    assert (await older).superseded
    assert session.of("error") == []
    assert session.of("render")[-1]["payload"]["tool_result"]["structuredContent"] == {"count": 2}


# ----------------------------------------------- rendering never executes


class FakeChat:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def append_message(self, message: Any) -> None:
        self.messages.append(message)


def chat_widget_id(chat: FakeChat) -> str:
    """The widget id inside the last assistant message appended by render_in_chat."""
    return str(chat.messages[-1]["content"][-1].attrs["data-widget-id"])


async def test_render_in_chat_never_calls_the_tool(env):
    host, session = env
    chat = FakeChat()
    result = await host.call_tool("fx", "increment", {"count": 4})
    out = await host.render_in_chat(chat, "fx", "increment", {"count": 4}, result, text="Here:")
    assert out.display == "view" and out.widget_id == chat_widget_id(chat)
    await mount(host, session, out.widget_id)
    msg = await session.wait_for(lambda m: m["kind"] == "render")
    assert msg["payload"]["tool_result"]["structuredContent"] == {"count": 5}
    assert (await stats(host))["increment"] == 1  # only the caller's own call


async def test_render_methods_require_an_outcome(env):
    host, _ = env
    chat = FakeChat()
    # Omitting the outcome used to execute the tool; now it is a programming error.
    with pytest.raises(ValueError, match="never call the tool"):
        await host.render_in_chat(chat, "fx", "increment", {"count": 1})
    with pytest.raises(ValueError, match="exactly one"):
        await host.render_result("fx", "increment", {"count": 1}, target="w1")
    with pytest.raises(ValueError, match="exactly one"):
        result = await host.call_tool("fx", "plain")
        await host.render_result("fx", "plain", {}, result, target="w1", error="both")
    assert chat.messages == []  # nothing appended for a rejected call
    assert (await stats(host))["increment"] == 0


async def test_render_in_chat_shows_a_failed_call_without_retrying(env):
    host, session = env
    chat = FakeChat()
    # A View tool whose call failed: the View is shown and told the call was cancelled.
    out = await host.render_in_chat(chat, "fx", "increment", {"count": 1}, error="agent aborted")
    assert out.display == "view" and out.tool_failed and out.result is None
    await mount(host, session, out.widget_id)
    msg = await session.wait_for(lambda m: m["kind"] == "render")
    assert msg["payload"]["tool_cancelled"] == {"reason": "agent aborted"}
    # A tool without a UI: the failure is shown as an error in the widget.
    out = await host.render_in_chat(chat, "fx", "plain", {}, error="server said no")
    assert out.display == "error" and out.tool_error == "server said no" and out.render_error is None
    await mount(host, session, out.widget_id, "m2")
    err = await session.wait_for(lambda m: m["kind"] == "error" and m["widget_id"] == out.widget_id)
    assert err["payload"] == {"code": "tool_failed", "message": "server said no"}
    assert (await stats(host))["increment"] == 0


async def test_call_and_render_reports_a_failed_call_as_tool_error(fixture_server):
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX, policy=HostPolicy(request_timeout=1.0))  # type: ignore[arg-type]
    host.add_server("fx", url=f"{fixture_server.url}/mcp")
    try:
        out = await host.call_and_render("fx", "slow", {"seconds": 5}, target="w1")
        assert out.display == "error" and out.tool_failed and "timed out" in out.tool_error
        assert out.render_error is None
    finally:
        await host.close()


# ------------------------------------------------------------ remove_widget


async def test_remove_widget_clears_status_context_and_requests(env):
    from shiny import reactive

    host, session = env
    host.policy = HostPolicy(app_tools={"fx": ["slow"]})
    events = []
    host.on_model_context(events.append)
    _, msg = await render(host, session)
    await session.request(host, kind="initialized", widget_id="w1", mount_id="m1", render_id=1)
    await view_request(host, session, msg, "model-context", {"structuredContent": {"pick": 1}}, "m1.1")
    with reactive.isolate():
        assert host.widget_status("w1") == "initialized"
        assert host.model_context("w1").structured_content == {"pick": 1}

    # An in-flight View request is cancelled and settled.
    slow = await session.request(
        host,
        kind="tools/call",
        widget_id="w1",
        mount_id="m1",
        request_id="m1.2",
        render_id=1,
        payload={"params": {"name": "slow", "arguments": {"seconds": 30}}},
    )
    assert slow == {"status": "pending"}
    await host.remove_widget("w1")

    with reactive.isolate():
        assert host.widget_status("w1") is None
        assert host.model_context("w1") is None
        assert host.model_context() == {}
    assert events[-1].widget_id == "w1" and events[-1].cleared
    assert session.of("teardown", "w1")
    resp = await session.wait_for(lambda m: m["kind"] == "response" and m.get("request_id") == "m1.2")
    assert resp["payload"]["error"]["code"] == "cancelled"
    # Later requests from the removed View are rejected as stale.
    stale = await session.request(
        host,
        kind="model-context",
        widget_id="w1",
        mount_id="m1",
        request_id="m1.3",
        render_id=1,
        payload={"params": {"structuredContent": {"pick": 2}}},
    )
    assert stale["ok"] is False
    with reactive.isolate():
        assert host.model_context("w1") is None
    # Removing again, or an unknown widget, is a no-op.
    await host.remove_widget("w1")
    await host.remove_widget("never-existed")


async def test_remove_widget_drops_a_render_in_progress(env):
    host, session = env
    await mount(host, session, "w1")
    pending = asyncio.create_task(host.call_and_render("fx", "slow-ui", {"seconds": 0.5}, target="w1"))
    await asyncio.sleep(0.1)
    await host.remove_widget("w1")
    assert (await pending).superseded
    assert session.of("render", "w1") == []
    from shiny import reactive

    with reactive.isolate():
        assert host.widget_status("w1") is None


async def test_packaged_demo_server_runs_without_the_repo(tmp_path):
    """The CI artifact: a folder deployable on its own, served via its ASGI `app`."""
    import subprocess
    import sys

    from tests import procs

    sys.path.insert(0, str(procs.ROOT / "scripts"))
    from package_demo_server import package  # type: ignore[import-not-found]

    folder = package(tmp_path / "sales-explorer-mcp-server")
    port = procs.free_port()
    proc = procs.Proc(
        "packaged-sales-server",
        [sys.executable, "-I", "-m", "uvicorn", "server:app", "--port", str(port)],
        port,
        env={"MCP_ALLOWED_HOSTS": f"127.0.0.1:{port}"},
        cwd=folder,
    )
    session = FakeSession()
    host = MCPHost(session, sandbox_url=SANDBOX)  # type: ignore[arg-type]
    host.add_server("sales", url=f"http://127.0.0.1:{port}/mcp")
    try:
        await mount(host, session, "w")
        out = await host.call_and_render("sales", "explore_sales", {"quarter": "Q3"}, target="w")
        assert out.display == "view" and out.result.structured_content["filters"]["quarter"] == "Q3"
        msg = await session.wait_for(lambda m: m["kind"] == "render")
        assert "query_sales" in msg["payload"]["resource"]["html"]  # bundle inlined
        # DNS-rebinding protection rejects an unexpected Host header.
        r = subprocess.run(
            [
                "curl",
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-H",
                "Host: evil.example",
                "-X",
                "POST",
                f"http://127.0.0.1:{port}/mcp",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.stdout.startswith("4"), r.stdout
    finally:
        await host.close()
        proc.stop()
