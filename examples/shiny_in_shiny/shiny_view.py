"""Spike: serve a Shiny for Python app as an MCP App View, with MCP tool calls as its "websocket".

A normal Shiny app talks to its Python session over a websocket. An MCP App View
cannot open one (the sandbox CSP allows no network by default, and no host has
to approve a domain for this), but it *can* call app-only tools on its own
server through the host. So:

    shiny.js ── Shiny.createSocket() → fake socket
             ── send(msg)   → app.callServerTool("shiny_rpc", {op: "send", sid, msgs})   (returns at once)
             ◀─ onmessage   ← a long-poll {op: "poll"}, always in flight, returns the session's
                              output as soon as there is some: {msgs: [[seq, msg], ...]}

    MCP server: shiny_rpc → McpConnection (a shiny Connection) → a real shiny AppSession

Every message is a tool call through the host, so the host's policy sees it and
no network access needs approving. The View is Shiny's own rendered page with
every dependency inlined, plus the MCP Apps SDK and a small shim.

Inside the Shiny server function, ``view(session)`` gives the MCP side:
``tool_input()`` (reactive), ``update_model_context()`` and ``send_message()``.

Spike limitations: output types that fetch from the session's HTTP routes
(``render.plot`` images, downloads, dynamic UI that adds new dependencies) do
not work; there is no auth on session ids beyond being unguessable; sessions
live in this process (single worker only) and are reaped when polls stop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from htmltools import HTMLDocument, Tag
from shiny import App, reactive
from shiny._connection import Connection, ConnectionClosed
from shiny.session import Session, session_context
from starlette.requests import HTTPConnection

from shiny_mcp_host.view import sdk_script

logger = logging.getLogger(__name__)

POLL_WAIT = 20.0  # how long a long-poll waits for output before returning empty (host timeout is 30s)
SETTLE = 0.03  # after output starts, keep collecting until this long passes with nothing new
IDLE_TIMEOUT = 60.0  # a session nobody has called for this long is closed (the View went away)


# --------------------------------------------------------------------------- connection


class McpConnection(Connection):
    """A shiny ``Connection`` whose other end is a series of MCP tool calls."""

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self._outbox: list[tuple[int, str]] = []
        self._seq = 0
        self._changed = asyncio.Event()
        self.closed = False
        self.last_seen = time.monotonic()
        self._http_conn = HTTPConnection(
            scope={"type": "websocket", "headers": [], "path": "/", "query_string": b""}
        )

    # Shiny side
    async def send(self, message: str) -> None:
        self._seq += 1
        self._outbox.append((self._seq, message))
        self._changed.set()

    async def receive(self) -> str:
        msg = await self._incoming.get()
        if msg is None:
            raise ConnectionClosed()
        return msg

    async def close(self, code: int, reason: str | None) -> None:
        self.closed = True
        self._changed.set()

    def get_http_conn(self) -> HTTPConnection:
        return self._http_conn

    # MCP side
    def push(self, msgs: list[str]) -> None:
        for m in msgs:
            self._incoming.put_nowait(m)

    def disconnect(self) -> None:
        self._incoming.put_nowait(None)

    def _take(self) -> list[tuple[int, str]]:
        out, self._outbox = self._outbox, []
        self._changed.clear()
        return out

    async def collect(self, wait: float) -> list[tuple[int, str]]:
        """Wait up to ``wait`` for output, then keep collecting until none arrives for SETTLE seconds."""
        self.last_seen = time.monotonic()
        deadline = time.monotonic() + wait
        got: list[tuple[int, str]] = []
        while time.monotonic() < deadline and not self.closed:
            try:
                await asyncio.wait_for(
                    self._changed.wait(), timeout=SETTLE if got else deadline - time.monotonic()
                )
            except TimeoutError:
                if got:
                    break
                continue
            got += self._take()
        got += self._take()
        self.last_seen = time.monotonic()
        return got


# --------------------------------------------------------------------------- per-session MCP bridge


class ViewBridge:
    """What the inner Shiny server function sees of MCP: ``view(session)``."""

    def __init__(self, session: Session, tool_input: dict[str, Any]) -> None:
        self._session = session
        self.tool_input = reactive.value(tool_input)

    async def update_model_context(self, data: dict[str, Any], text: str) -> None:
        """Share state with the host's conversation (MCP Apps ``ui/update-model-context``)."""
        await self._session.send_custom_message("mcp:update-model-context", {"data": data, "text": text})

    async def send_message(self, text: str) -> None:
        """Ask the host to add a user message to the conversation (``ui/message``)."""
        await self._session.send_custom_message("mcp:send-message", {"text": text})


_BRIDGES: dict[str, ViewBridge] = {}


def view(session: Session) -> ViewBridge:
    return _BRIDGES[session.id]


# --------------------------------------------------------------------------- the host of inner sessions


class ShinyView:
    """Runs one Shiny ``App`` as an MCP App View: page HTML plus the session RPC."""

    def __init__(self, app: App, page: Tag, *, rpc_tool: str = "shiny_rpc", title: str = "Shiny") -> None:
        self.app = app
        self.page = page  # the app's UI object (App keeps only its rendered form)
        self.rpc_tool = rpc_tool
        self.title = title
        self._conns: dict[str, McpConnection] = {}
        self._reaper: asyncio.Task | None = None

    # ---- page

    def page_html(self) -> str:
        """Shiny's rendered page with every dependency inlined, plus the MCP Apps SDK and the socket shim."""
        rendered = HTMLDocument(self.page).render(lib_prefix="__lib__")
        html: str = rendered["html"]
        dirs = {
            m["href"]: m["source"]
            for d in rendered["dependencies"]
            if (m := d.source_path_map(lib_prefix="__lib__"))["href"]
        }

        def path(href: str) -> Path:
            for prefix, source in dirs.items():
                if href.startswith(prefix + "/"):
                    return Path(source) / href[len(prefix) + 1 :]
            raise FileNotFoundError(href)

        def read(href: str) -> str:
            return path(href).read_text(encoding="utf-8")

        def css_urls(css: str, base: Path) -> str:
            # Relative url(...) in CSS (spinners, icons) become data: URIs; the View has no server.
            def data_uri(m: re.Match[str]) -> str:
                ref = m.group(2)
                if re.match(r"(data:|https?:|#)", ref):
                    return m.group(0)
                f = (base / ref.split("?")[0].split("#")[0]).resolve()
                if not f.is_file() or f.suffix == ".css":  # an @import'ed web font sheet: skipped
                    return m.group(0)
                mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return f'url("data:{mime};base64,{base64.b64encode(f.read_bytes()).decode()}")'

            return re.sub(r"""url\((['"]?)([^'")]+)\1\)""", data_uri, css)

        def script(m: re.Match[str]) -> str:
            attrs = m.group(1) + m.group(3)
            return f"<script{attrs}>{_inline_js(read(m.group(2)))}</script>"

        def style(m: re.Match[str]) -> str:
            css = css_urls(read(m.group(1)), path(m.group(1)).parent).replace("</style", "<\\/style")
            return f"<style>{css}</style>"

        html = re.sub(r'<script([^>]*?) src="(__lib__/[^"]+)"([^>]*)></script>', script, html)
        html = re.sub(r'<link href="(__lib__/[^"]+)" rel="stylesheet"/>', style, html)
        if "__lib__/" in html:
            raise RuntimeError("ShinyView: a dependency could not be inlined")
        shim = _SHIM.replace("__RPC_TOOL__", json.dumps(self.rpc_tool)).replace(
            "__TITLE__", json.dumps(self.title)
        )
        # Before shiny.js runs: the MCP Apps SDK (module, deferred) and the socket shim (classic,
        # runs right after shiny.js because it is placed at the end of <head>). Shiny initialises
        # on DOM ready, after both.
        head_extra = (
            f'<script type="module">{_inline_js(sdk_script())}</script>\n'
            f"<script>{_inline_js(shim)}</script>\n</head>"
        )
        before, sep, after = html.rpartition("</head>")  # the last one: shiny.js itself contains "</head>"
        return before + head_extra + after

    # ---- RPC

    async def handle(self, op: str, sid: str | None, msgs: list[str] | None, tool_input: dict | None) -> dict:
        self._ensure_reaper()
        if op == "open":
            conn = McpConnection()
            session = self.app._create_session(conn)
            _BRIDGES[session.id] = ViewBridge(session, tool_input or {})
            self._conns[session.id] = conn
            task = asyncio.create_task(self._run(session, conn))
            task.add_done_callback(lambda _t: None)
            return {"sid": session.id, "msgs": []}
        conn = self._conns.get(sid or "")
        if conn is None or conn.closed:
            return {"closed": True, "msgs": []}
        if op == "send":
            # Fire and forget: all output comes back through the poll that is always in flight,
            # so a send never waits for (or races the poll over) the session's response.
            conn.push(list(msgs or []))
            return {"msgs": []}
        if op == "poll":
            return {"msgs": await conn.collect(POLL_WAIT), "closed": conn.closed}
        if op == "close":
            conn.disconnect()
            return {"closed": True, "msgs": []}
        raise ValueError(f"unknown op {op!r}")

    async def _run(self, session: Any, conn: McpConnection) -> None:
        try:
            await session._run()
        except Exception:  # noqa: BLE001 - a spike: log and drop the session
            logger.exception("Shiny session %s failed", session.id)
        finally:
            conn.closed = True
            conn._changed.set()
            self._conns.pop(session.id, None)
            _BRIDGES.pop(session.id, None)
            self.app._sessions.pop(session.id, None)

    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(IDLE_TIMEOUT / 4)
            now = time.monotonic()
            for conn in list(self._conns.values()):
                if now - conn.last_seen > IDLE_TIMEOUT:
                    conn.disconnect()

    # ---- driving a live session from outside (e.g. a model-facing tool)

    def sessions(self) -> list[str]:
        return [sid for sid, c in self._conns.items() if not c.closed]

    async def run_in(self, sid: str, fn: Callable[[Session], Any]) -> None:
        """Run ``fn(session)`` inside a live session and flush, so its updates reach the View's next poll."""
        session = self.app._sessions.get(sid)
        if session is None:
            raise KeyError(f"No live Shiny session {sid!r}")
        from shiny.reactive._core import flush, lock

        async with lock():
            with session_context(session):
                result = fn(session)
                if asyncio.iscoroutine(result):
                    await result
            await flush()


def _inline_js(js: str) -> str:
    return js.replace("</script", "<\\/script").replace("<!--", "<\\!--")


# The browser side. Runs as a classic script right after shiny.js and before Shiny initialises.
_SHIM = r"""
(() => {
  const RPC = __RPC_TOOL__;
  let resolveApp;
  const appReady = new Promise((r) => (resolveApp = r));
  let resolveInput;
  const toolInput = new Promise((r) => (resolveInput = r));
  setTimeout(() => resolveInput({}), 3000); // a host that never sends tool input

  // The MCP Apps SDK is a deferred module; start it once it has run.
  window.addEventListener("DOMContentLoaded", async () => {
    const app = new McpApps.App({ name: __TITLE__, version: "0.1.0" });
    app.ontoolinput = (p) => resolveInput(p.arguments || {});
    app.ontoolresult = (r) => resolveInput(r.structuredContent || {});
    await app.connect();
    resolveApp(app);
    Shiny.addCustomMessageHandler("mcp:update-model-context", (m) =>
      app.updateModelContext({ content: [{ type: "text", text: m.text }], structuredContent: m.data }));
    Shiny.addCustomMessageHandler("mcp:send-message", (m) =>
      app.sendMessage({ role: "user", content: [{ type: "text", text: m.text }] }));
  });

  async function rpc(args) {
    const app = await appReady;
    const r = await app.callServerTool({ name: RPC, arguments: args });
    if (r.isError) throw new Error(r.content?.[0]?.text || "shiny_rpc failed");
    return r.structuredContent;
  }

  // A WebSocket look-alike for shiny.js: readyState, send, close, on* handlers.
  window.Shiny.createSocket = () => {
    const sock = { readyState: 0, binaryType: "arraybuffer", allowReconnect: false };
    let sid = null, nextSeq = 1, sendChain = Promise.resolve(), outbuf = [];
    const held = new Map();

    function deliver(res) {
      for (const [seq, msg] of res.msgs || []) held.set(seq, msg);
      while (held.has(nextSeq)) {
        const msg = held.get(nextSeq);
        held.delete(nextSeq++);
        sock.onmessage && sock.onmessage({ data: msg });
      }
      if (res.closed) shut();
    }
    function shut() {
      if (sock.readyState === 3) return;
      sock.readyState = 3;
      sock.onclose && sock.onclose({ code: 1000 });
    }
    function flush() {
      const msgs = outbuf;
      outbuf = [];
      if (!msgs.length) return;
      sendChain = sendChain.then(() => rpc({ op: "send", sid, msgs })).then(deliver, (e) => { console.error(e); shut(); });
    }
    async function pollLoop() {
      while (sock.readyState === 1) {
        try { deliver(await rpc({ op: "poll", sid })); }
        catch (e) { console.error(e); await new Promise((r) => setTimeout(r, 1000)); }
      }
    }
    sock.send = (msg) => { outbuf.push(msg); queueMicrotask(flush); };
    sock.close = () => { if (sid) rpc({ op: "close", sid }).catch(() => {}); shut(); };

    (async () => {
      try {
        const input = await toolInput;
        const res = await rpc({ op: "open", tool_input: input });
        sid = res.sid;
        sock.readyState = 1;
        sock.onopen && sock.onopen();
        deliver(res);
        pollLoop();
      } catch (e) {
        console.error("Could not start the Shiny session", e);
        shut();
      }
    })();
    return sock;
  };
})();
"""
