"""Public API: :class:`MCPHost`, one per Shiny session.

Python owns every MCP connection. The browser adapter (``static/shiny-mcp-host.js``)
runs one official ``AppBridge`` per widget and relays only validated requests here
through ``Shiny.shinyapp.makeRequest``; responses and render instructions go back
via ``session.send_custom_message``. See ``docs/protocol-contract.md``.
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import logging
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

from htmltools import Tag, TagList, tags
from mcp.types import CallToolResult, Tool
from shiny import reactive
from shiny.reactive._core import get_current_context
from shiny.session import Session, require_active_session

from .client import ServerConfig, ServerConnection, Transport
from .components import DEFAULT_HOST_ID, widget_tag
from .errors import (
    MCPHostError,
    PolicyError,
    ResourceError,
    SandboxConfigError,
)
from .policy import (
    HostPolicy,
    sanitize_csp,
    sanitize_permissions,
    tool_visibility,
    validate_open_link,
)
from .protocol import Envelope, extract_ui_html, get_tool_ui_resource_uri, parse_envelope, to_wire

__all__ = (
    "MCPHost",
    "ToolInfo",
    "RenderResult",
    "ModelContextEvent",
    "AppMessageEvent",
    "SANDBOX_URL_ENV",
)

logger = logging.getLogger("shiny_mcp_host")

#: Environment variable consulted when ``sandbox_url`` is not passed.
SANDBOX_URL_ENV = "SHINY_MCP_SANDBOX_URL"
#: Custom message type used for every Python -> browser message.
CUSTOM_MESSAGE_TYPE = "shiny-mcp-host"

WidgetStatus = Literal["waiting", "loading", "rendered", "initialized", "error"]


@dataclass(frozen=True)
class ToolInfo:
    """A tool as listed by a server, with its MCP Apps metadata resolved."""

    server: str
    name: str
    title: str | None
    description: str | None
    ui_resource_uri: str | None
    visibility: tuple[str, ...]
    tool: Tool = field(repr=False)

    @property
    def has_ui(self) -> bool:
        return self.ui_resource_uri is not None


@dataclass(frozen=True)
class RenderResult:
    """Outcome of :meth:`MCPHost.call_and_render`."""

    widget_id: str
    server: str
    tool: str
    result: CallToolResult | None
    resource_uri: str | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class ModelContextEvent:
    """A ``ui/update-model-context`` request from one View."""

    widget_id: str
    server: str
    tool: str
    content: list[dict[str, Any]] | None
    structured_content: dict[str, Any] | None

    @property
    def cleared(self) -> bool:
        return not self.content and not self.structured_content


@dataclass(frozen=True)
class AppMessageEvent:
    """A ``ui/message`` request (the View asks to add a message to the conversation)."""

    widget_id: str
    server: str
    tool: str
    role: str
    content: list[dict[str, Any]]

    @property
    def text(self) -> str:
        return "\n".join(
            str(c.get("text", "")) for c in self.content if isinstance(c, Mapping) and c.get("type") == "text"
        )


@dataclass
class _Render:
    render_id: int
    server: str
    tool: str
    message: dict[str, Any]


@dataclass
class _Widget:
    id: str
    mount_id: str | None = None
    render: _Render | None = None
    status: WidgetStatus = "waiting"
    status_message: dict[str, Any] | None = None
    tasks: dict[asyncio.Task[Any], tuple[str, str]] = field(default_factory=dict)
    render_seq: int = 0


Callback = Callable[[Any], Any] | Callable[[Any], Awaitable[Any]]


async def _set_reactive(fn: Callable[[], None]) -> None:
    """Mutate reactive state from either a reactive context or a plain task.

    Inside a reactive context (an effect, or a message handler) the session
    already holds the reactive lock, so acquiring it again would deadlock.
    """
    try:
        get_current_context()
    except RuntimeError:
        async with reactive.lock():
            fn()
            await reactive.flush()
    else:
        fn()


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


class MCPHost:
    """Hosts MCP Apps for one Shiny session.

    Parameters
    ----------
    session
        The Shiny session (defaults to the current session).
    id
        Host id; widgets created with ``mcp_widget(..., host=id)`` are driven by
        this host. Namespaced inside Shiny modules.
    sandbox_url
        URL of the sandbox proxy page (``.../sandbox.html``) served from an
        origin distinct from the Shiny app, e.g. by
        :func:`shiny_mcp_host.sandbox.start_sandbox_server`. Falls back to the
        ``SHINY_MCP_SANDBOX_URL`` environment variable. There is no unsafe
        fallback: without it, :class:`SandboxConfigError` is raised.
    policy
        Authorisation and limits; see :class:`HostPolicy`.
    theme
        ``"light"`` or ``"dark"``, passed to Views in the host context.
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        id: str = DEFAULT_HOST_ID,
        sandbox_url: str | None = None,
        policy: HostPolicy | None = None,
        theme: Literal["light", "dark"] = "light",
    ) -> None:
        self.session = require_active_session(session)
        self.id = self.session.ns(id)
        self.policy = policy or HostPolicy()
        self.theme = theme
        self.sandbox_url = self._validate_sandbox_url(sandbox_url or os.environ.get(SANDBOX_URL_ENV))
        self._servers: dict[str, ServerConnection] = {}
        self._widgets: dict[str, _Widget] = {}
        self._model_context_callbacks: list[Callback] = []
        self._message_callbacks: list[Callback] = []
        self._model_context: reactive.Value[dict[str, ModelContextEvent]] = reactive.Value({})
        self._statuses: reactive.Value[dict[str, WidgetStatus]] = reactive.Value({})
        self._auto_ids = itertools.count(1)
        self._closed = False
        self._handler_name = f"shiny-mcp-host:{self.id}"
        root = self.session.root_scope()
        root.set_message_handler(self._handler_name, self._on_client_message)
        self.session.on_ended(self.close)

    # ------------------------------------------------------------------ config

    def _validate_sandbox_url(self, url: str | None) -> str:
        if not url:
            raise SandboxConfigError(
                "No sandbox origin configured. Pass sandbox_url= (see "
                "shiny_mcp_host.sandbox.start_sandbox_server) or set "
                f"{SANDBOX_URL_ENV}. Refusing to render MCP Apps without a "
                "distinct-origin sandbox."
            )
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise SandboxConfigError(f"Invalid sandbox URL: {url!r}")
        conn = getattr(self.session, "http_conn", None)
        if conn is not None:
            try:
                host = conn.headers.get("host")
                scheme = "https" if conn.url.scheme in ("https", "wss") else "http"
                app_origin = f"{scheme}://{host}".lower() if host else None
            except Exception:  # noqa: BLE001 - best effort; the browser re-checks
                app_origin = None
            if app_origin and app_origin == _origin(url):
                raise SandboxConfigError(
                    f"Sandbox origin {_origin(url)} is the same as the Shiny app origin; "
                    "a distinct origin (different host or port) is required."
                )
        return url

    def add_server(
        self,
        name: str,
        *,
        url: str | None = None,
        transport: Transport = "streamable_http",
        command: str | None = None,
        args: list[str] | tuple[str, ...] = (),
        env: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        auth: Any = None,
    ) -> None:
        """Register an MCP server. Connection happens lazily on first use.

        ``headers`` / ``auth`` (an ``httpx2.Auth``) are the extension point for
        authenticated remote servers; they never leave Python.
        """
        if name in self._servers:
            raise ValueError(f"Server {name!r} is already registered")
        config = ServerConfig(
            name=name,
            transport=transport,
            url=url,
            command=command,
            args=tuple(args),
            env=env,
            headers=headers,
            auth=auth,
        )
        self._servers[name] = ServerConnection(config, timeout=self.policy.request_timeout)

    def _server(self, name: str) -> ServerConnection:
        try:
            return self._servers[name]
        except KeyError:
            raise MCPHostError(f"Unknown MCP server: {name!r}") from None

    # ------------------------------------------------------------- MCP access

    async def list_tools(self, server: str, *, refresh: bool = False) -> list[ToolInfo]:
        """List a server's tools with resolved MCP Apps metadata."""
        conn = self._server(server)
        infos: list[ToolInfo] = []
        for tool in await conn.list_tools(refresh=refresh):
            try:
                uri = get_tool_ui_resource_uri(tool)
            except ResourceError as e:
                logger.warning("Ignoring invalid UI metadata on tool %r: %s", tool.name, e)
                uri = None
            infos.append(
                ToolInfo(
                    server=server,
                    name=tool.name,
                    title=tool.title,
                    description=tool.description,
                    ui_resource_uri=uri,
                    visibility=tuple(tool_visibility(tool)),
                    tool=tool,
                )
            )
        return infos

    async def call_tool(
        self, server: str, tool: str, arguments: Mapping[str, Any] | None = None
    ) -> CallToolResult:
        """Call a tool without rendering (e.g. for an LLM tool loop)."""
        return await self._server(server).call_tool(tool, arguments)

    # -------------------------------------------------------------- rendering

    def widget(self, id: str | None = None, *, height: int | str = 360) -> Tag:
        """Return a widget mount point bound to this host (auto id if omitted).

        Use the returned tag in any UI, including a ``ui.Chat`` message; pass
        its id (``tag.attrs["data-widget-id"]``) as ``target`` to
        :meth:`call_and_render`.
        """
        if id is None:
            widget_id = f"{self.id}_widget_{next(self._auto_ids)}_{secrets.token_hex(3)}"
        else:
            widget_id = self.session.ns(id)
        return widget_tag(widget_id, self.id, height)

    def _resolve_target(self, target: str | Tag) -> str:
        if isinstance(target, Tag):
            wid = target.attrs.get("data-widget-id")
            if not wid:
                raise ValueError("Tag is not an MCP widget")
            return str(wid)
        return self.session.ns(target)

    async def call_and_render(
        self,
        server: str,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        target: str | Tag,
    ) -> RenderResult:
        """Call ``tool`` on ``server`` and render its MCP App View in ``target``.

        The widget may be mounted before or after this call; the View is started
        once both the DOM element and the render data are available. Errors are
        shown in the widget and returned in :attr:`RenderResult.error`.
        """
        widget_id = self._resolve_target(target)
        w = self._widget(widget_id)
        args = dict(arguments or {})
        w.render_seq += 1
        render_id = w.render_seq
        await self._set_status(w, "loading", {"kind": "loading", "payload": {"label": f"Running {tool}…"}})
        conn = self._server(server)
        result: CallToolResult | None = None
        uri: str | None = None
        try:
            tool_def = await conn.get_tool(tool)
            if tool_def is None:
                raise MCPHostError(f"Server {server!r} has no tool {tool!r}")
            uri = get_tool_ui_resource_uri(tool_def)
            call = asyncio.ensure_future(conn.call_tool(tool, args))
            if uri is None:
                result = await call
                await self._push_fallback(w, server, tool, result)
                return RenderResult(widget_id, server, tool, result, None)
            read = asyncio.ensure_future(conn.read_resource(uri))
            done = await asyncio.gather(call, read, return_exceptions=True)
            call_out, read_out = done
            cancelled: dict[str, Any] | None = None
            if isinstance(call_out, BaseException):
                cancelled = {"reason": str(call_out) or type(call_out).__name__}
            else:
                result = call_out
            if isinstance(read_out, BaseException):
                raise read_out
            html, ui_meta = extract_ui_html(
                read_out, expected_uri=uri, max_bytes=self.policy.max_resource_bytes
            )
            if w.render_seq != render_id:
                return RenderResult(widget_id, server, tool, result, uri, error="superseded")
            message = self._render_message(
                render_id, server, tool_def, uri, html, ui_meta, args, result, cancelled
            )
            w.render = _Render(render_id, server, tool, message)
            w.status_message = None
            await self._cancel_widget_tasks(w)
            await self._set_status(w, "rendered", None)
            await self._push_render(w)
            return RenderResult(
                widget_id, server, tool, result, uri, error=cancelled["reason"] if cancelled else None
            )
        except MCPHostError as e:
            await self._push_error(w, e.code, str(e), result)
            return RenderResult(widget_id, server, tool, result, uri, error=str(e))

    async def render_in_chat(
        self,
        chat: Any,
        server: str,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        text: str | None = None,
        height: int | str = 360,
    ) -> RenderResult:
        """Append an assistant message containing a new widget to a ``ui.Chat`` and render into it."""
        tag = self.widget(height=height)
        content = TagList(tag) if text is None else TagList(tags.p(text), tag)
        await chat.append_message({"role": "assistant", "content": content})
        return await self.call_and_render(server, tool, arguments, target=tag)

    def _render_message(
        self,
        render_id: int,
        server: str,
        tool: Tool,
        uri: str,
        html: str,
        ui_meta: Mapping[str, Any],
        arguments: dict[str, Any],
        result: CallToolResult | None,
        cancelled: dict[str, Any] | None,
    ) -> dict[str, Any]:
        csp, rejected = sanitize_csp(ui_meta.get("csp"), self.policy)
        if rejected:
            logger.warning("Dropped unapproved CSP sources for %s: %s", uri, rejected)
        permissions = sanitize_permissions(ui_meta.get("permissions"), self.policy)
        logger.info("Rendering %s (server=%s, csp=%s, permissions=%s)", uri, server, csp, permissions)
        return {
            "render_id": render_id,
            "server": server,
            "tool": tool.name,
            "resource": {
                "uri": uri,
                "html": html,
                "csp": csp,
                "permissions": permissions,
                "prefers_border": bool(ui_meta.get("prefersBorder", False)),
            },
            "tool_input": {"arguments": arguments},
            "tool_result": to_wire(result) if result is not None else None,
            "tool_cancelled": cancelled,
            "sandbox": {"url": self.sandbox_url, "view_same_origin": self.policy.view_same_origin},
            "host_context": {
                "theme": self.theme,
                "platform": "web",
                "displayMode": "inline",
                "availableDisplayModes": ["inline"],
                "toolInfo": {"tool": to_wire(tool)},
            },
            "capabilities": {
                "open_links": self.policy.allow_open_links,
                "resource_read": self.policy.allow_resource_read,
            },
        }

    async def remove_widget(self, target: str | Tag) -> None:
        """Tear down a widget's View and forget its state."""
        widget_id = self._resolve_target(target)
        w = self._widgets.pop(widget_id, None)
        if w is None:
            return
        await self._cancel_widget_tasks(w)
        await self._send(w.id, "teardown", {})
        self._update_status_value(widget_id, None)

    # -------------------------------------------------------------- callbacks

    def on_model_context(self, fn: Callback) -> Callback:
        """Register ``fn(event: ModelContextEvent)`` for View model-context updates.

        Usable as a decorator. Callbacks run in the session's reactive context.
        """
        self._model_context_callbacks.append(fn)
        return fn

    def on_message(self, fn: Callback) -> Callback:
        """Register ``fn(event: AppMessageEvent)`` for ``ui/message`` requests.

        Return ``False`` to reject the message. Without any callback, messages
        are rejected (``isError: true``).
        """
        self._message_callbacks.append(fn)
        return fn

    def model_context(self, target: str | Tag | None = None) -> Any:
        """Reactive read of the latest model context (per widget, or all widgets)."""
        contexts = self._model_context()
        if target is None:
            return dict(contexts)
        return contexts.get(self._resolve_target(target))

    def widget_status(self, target: str | Tag) -> WidgetStatus | None:
        """Reactive read of a widget's lifecycle status."""
        return self._statuses().get(self._resolve_target(target))

    # ----------------------------------------------------------- browser RPC

    def _widget(self, widget_id: str) -> _Widget:
        w = self._widgets.get(widget_id)
        if w is None:
            w = self._widgets[widget_id] = _Widget(widget_id)
        return w

    async def _send(self, widget_id: str, kind: str, payload: dict[str, Any], **extra: Any) -> None:
        if self._closed:
            return
        message = {
            "v": 1,
            "host_id": self.id,
            "widget_id": widget_id,
            "kind": kind,
            "payload": payload,
            **extra,
        }
        try:
            await self.session.send_custom_message(CUSTOM_MESSAGE_TYPE, message)
        except Exception as e:  # noqa: BLE001 - session may be closing
            logger.debug("Could not send %s to %s: %r", kind, widget_id, e)

    def _update_status_value(self, widget_id: str, status: WidgetStatus | None) -> None:
        with reactive.isolate():
            current = dict(self._statuses())
        if status is None:
            current.pop(widget_id, None)
        else:
            current[widget_id] = status
        self._statuses.set(current)

    async def _set_status(self, w: _Widget, status: WidgetStatus, message: dict[str, Any] | None) -> None:
        w.status = status
        if message is not None:
            w.status_message = message
            await self._send(w.id, message["kind"], message["payload"])
        await _set_reactive(lambda: self._update_status_value(w.id, status))

    async def _push_render(self, w: _Widget) -> None:
        if w.render is not None and w.mount_id is not None:
            await self._send(w.id, "render", w.render.message, mount_id=w.mount_id)

    async def _push_error(self, w: _Widget, code: str, message: str, result: CallToolResult | None) -> None:
        payload: dict[str, Any] = {"code": code, "message": message}
        if result is not None:
            payload["fallback_text"] = _result_text(result)
        w.render = None
        await self._cancel_widget_tasks(w)
        await self._set_status(w, "error", {"kind": "error", "payload": payload})

    async def _push_fallback(self, w: _Widget, server: str, tool: str, result: CallToolResult) -> None:
        w.render = None
        payload = {
            "server": server,
            "tool": tool,
            "text": _result_text(result),
            "is_error": bool(result.is_error),
        }
        await self._set_status(w, "rendered", {"kind": "fallback", "payload": payload})

    async def _cancel_widget_tasks(self, w: _Widget) -> None:
        """Cancel a widget's in-flight View requests and settle each one in the browser."""
        tasks, w.tasks = w.tasks, {}
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        payload = {"ok": False, "error": {"code": "cancelled", "message": "Request cancelled"}}
        for t, (mount_id, request_id) in tasks.items():
            if t.cancelled():
                await self._send(w.id, "response", payload, mount_id=mount_id, request_id=request_id)

    async def _on_client_message(self, raw: object) -> dict[str, Any]:
        """Entry point for every browser -> Python envelope. Must return quickly."""
        try:
            env = parse_envelope(raw)
            return await self._dispatch(env)
        except MCPHostError as e:
            logger.info("Rejected browser request: %s", e)
            return {"status": "done", "ok": False, "error": {"code": e.code, "message": str(e)}}

    async def _dispatch(self, env: Envelope) -> dict[str, Any]:
        done_ok: dict[str, Any] = {"status": "done", "ok": True, "result": {}}
        if env.kind == "mount":
            w = self._widget(env.widget_id)
            if w.mount_id != env.mount_id:
                await self._cancel_widget_tasks(w)
            w.mount_id = env.mount_id
            if w.render is not None:
                await self._push_render(w)
            elif w.status_message is not None:
                await self._send(w.id, w.status_message["kind"], w.status_message["payload"])
            return done_ok

        w = self._widgets.get(env.widget_id)
        if w is None or w.mount_id != env.mount_id:
            raise MCPHostError("Stale or unknown widget mount")

        if env.kind == "unmount":
            w.mount_id = None
            await self._cancel_widget_tasks(w)
            if w.status == "initialized":
                w.status = "rendered"
                self._update_status_value(w.id, "rendered")
            return done_ok
        if env.kind == "view-error":
            message = str(env.payload.get("message", ""))[:2000]
            logger.warning("View error in widget %s: %s", w.id, message)
            w.status = "error"
            self._update_status_value(w.id, "error")
            return done_ok

        render = w.render
        if render is None or env.render_id != render.render_id:
            raise MCPHostError("Request from a stale View")

        if env.kind == "initialized":
            w.status = "initialized"
            self._update_status_value(w.id, "initialized")
            return done_ok
        if env.kind == "log":
            logger.info("[view %s] %s", w.id, json.dumps(env.payload.get("params"))[:2000])
            return done_ok
        if env.kind == "model-context":
            return await self._handle_model_context(w, render, env.payload.get("params"))
        if env.kind == "message":
            return await self._handle_message(w, render, env.payload.get("params"))
        if env.kind == "open-link":
            if not self.policy.allow_open_links:
                raise PolicyError("Opening links is disabled by policy")
            params = env.payload.get("params")
            url = validate_open_link(params.get("url") if isinstance(params, Mapping) else None)
            return {"status": "done", "ok": True, "result": {"url": url}}
        if env.kind in ("tools/call", "resources/read"):
            return self._start_request(w, render, env)
        raise MCPHostError(f"Unhandled kind {env.kind!r}")

    def _start_request(self, w: _Widget, render: _Render, env: Envelope) -> dict[str, Any]:
        if env.request_id is None:
            raise MCPHostError("request_id is required")
        if len(w.tasks) >= self.policy.max_pending_per_widget:
            raise PolicyError("Too many concurrent requests from this View")
        params = env.payload.get("params")
        if not isinstance(params, Mapping):
            raise MCPHostError("params must be an object")
        handler = self._view_tool_call if env.kind == "tools/call" else self._view_resource_read
        if env.kind == "resources/read" and not self.policy.allow_resource_read:
            raise PolicyError("resources/read is disabled by policy")
        task = asyncio.create_task(
            self._respond(w, env, lambda: handler(render, dict(params))),
            name=f"mcp-view:{w.id}:{env.request_id}",
        )
        w.tasks[task] = (env.mount_id, env.request_id)
        task.add_done_callback(lambda t: w.tasks.pop(t, None))
        return {"status": "pending"}

    async def _respond(self, w: _Widget, env: Envelope, run: Callable[[], Awaitable[Any]]) -> None:
        try:
            result = await run()
            payload: dict[str, Any] = {"ok": True, "result": result}
        except MCPHostError as e:
            payload = {"ok": False, "error": {"code": e.code, "message": str(e)}}
        except Exception as e:  # noqa: BLE001
            logger.exception("Unexpected error handling View request")
            payload = {"ok": False, "error": {"code": "host_error", "message": type(e).__name__}}
        await self._send(w.id, "response", payload, mount_id=env.mount_id, request_id=env.request_id)

    async def _view_tool_call(self, render: _Render, params: dict[str, Any]) -> Any:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            raise MCPHostError("Invalid tools/call params")
        if len(json.dumps(arguments)) > self.policy.max_tool_payload_bytes:
            raise PolicyError("Tool arguments exceed the size limit")
        conn = self._server(render.server)
        tool = await conn.get_tool(name)
        self.policy.check_app_tool_call(
            view_server=render.server,
            target_server=render.server,
            tool_name=name,
            origin_tool=render.tool,
            tool=tool,
        )
        logger.info("View tools/call %s.%s", render.server, name)
        return to_wire(await conn.call_tool(name, arguments))

    async def _view_resource_read(self, render: _Render, params: dict[str, Any]) -> Any:
        uri = params.get("uri")
        if not isinstance(uri, str) or len(uri) > 2048:
            raise MCPHostError("Invalid resources/read params")
        result = to_wire(await self._server(render.server).read_resource(uri))
        if len(json.dumps(result)) > self.policy.max_resource_bytes * 2:
            raise ResourceError("Resource exceeds the size limit")
        return result

    async def _handle_model_context(self, w: _Widget, render: _Render, params: Any) -> dict[str, Any]:
        if not isinstance(params, Mapping):
            raise MCPHostError("Invalid model context params")
        if len(json.dumps(params)) > self.policy.max_tool_payload_bytes:
            raise PolicyError("Model context exceeds the size limit")
        content = params.get("content")
        structured = params.get("structuredContent")
        event = ModelContextEvent(
            widget_id=w.id,
            server=render.server,
            tool=render.tool,
            content=list(content) if isinstance(content, list) else None,
            structured_content=dict(structured) if isinstance(structured, Mapping) else None,
        )
        with reactive.isolate():
            contexts = dict(self._model_context())
        if event.cleared:
            contexts.pop(w.id, None)
        else:
            contexts[w.id] = event
        self._model_context.set(contexts)
        for cb in list(self._model_context_callbacks):
            await _maybe_await(cb(event))
        return {"status": "done", "ok": True, "result": {}}

    async def _handle_message(self, w: _Widget, render: _Render, params: Any) -> dict[str, Any]:
        if not isinstance(params, Mapping) or not isinstance(params.get("content"), list):
            raise MCPHostError("Invalid message params")
        if len(json.dumps(params)) > self.policy.max_tool_payload_bytes:
            raise PolicyError("Message exceeds the size limit")
        event = AppMessageEvent(
            widget_id=w.id,
            server=render.server,
            tool=render.tool,
            role=str(params.get("role", "user")),
            content=list(params["content"]),
        )
        accepted = bool(self._message_callbacks)
        for cb in list(self._message_callbacks):
            if await _maybe_await(cb(event)) is False:
                accepted = False
        return {"status": "done", "ok": True, "result": {} if accepted else {"isError": True}}

    # ---------------------------------------------------------------- cleanup

    async def close(self) -> None:
        """Cancel pending requests, tear down Views and close MCP transports."""
        if self._closed:
            return
        for w in list(self._widgets.values()):
            await self._cancel_widget_tasks(w)
        self._closed = True
        try:
            self.session.root_scope().set_message_handler(self._handler_name, None)
        except Exception:  # noqa: BLE001
            pass
        self._widgets.clear()
        await asyncio.gather(*(c.close() for c in self._servers.values()), return_exceptions=True)
        logger.info("MCPHost %s closed", self.id)


def _result_text(result: CallToolResult) -> str:
    parts = [getattr(c, "text", None) for c in result.content]
    return "\n".join(p for p in parts if isinstance(p, str))[:20000]
