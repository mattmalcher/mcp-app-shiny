"""Server-side MCP connections (Python owns every MCP transport).

Each :class:`ServerConnection` runs its MCP ``Client`` inside a dedicated owner
task, because the SDK's anyio task groups must be entered and exited from the
same task. Requests may be issued concurrently from any task.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp import Client
from mcp.client import advertise
from mcp.client.stdio import StdioServerParameters
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult, Implementation, ReadResourceResult, Tool

from .errors import (
    MCPHostError,
    RequestTimeoutError,
    ServerUnavailableError,
    ToolCallError,
)
from .protocol import EXTENSION_ID, RESOURCE_MIME_TYPE

__all__ = ("ServerConfig", "ServerConnection")

logger = logging.getLogger("shiny_mcp_host")

CLIENT_INFO = Implementation(name="shiny-mcp-host", version="0.1.0")

Transport = Literal["streamable_http", "stdio"]


@dataclass(frozen=True)
class ServerConfig:
    """How to reach one MCP server.

    ``headers`` / ``auth`` are the extension point for remote authentication
    (e.g. a bearer token or an ``httpx2.Auth`` implementation); they stay in
    Python and are never sent to the browser.
    """

    name: str
    transport: Transport = "streamable_http"
    url: str | None = None
    command: str | None = None
    args: Sequence[str] = ()
    env: Mapping[str, str] | None = None
    headers: Mapping[str, str] | None = field(default=None, repr=False)
    auth: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.transport == "streamable_http":
            if not self.url or not self.url.startswith(("http://", "https://")):
                raise ValueError("streamable_http transport requires an http(s) url")
        elif self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio transport requires a command")
        else:
            raise ValueError(f"Unsupported transport: {self.transport!r}")


def _unwrap(exc: BaseException) -> BaseException:
    """Return the first leaf of a (possibly nested) exception group."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


class ServerConnection:
    """A lazily-connected MCP client for one server."""

    def __init__(self, config: ServerConfig, *, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout
        self._client: Client | None = None
        self._owner: asyncio.Task[None] | None = None
        self._closing: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._tools: dict[str, Tool] | None = None
        self._closed = False

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def connected(self) -> bool:
        return self._client is not None and self._owner is not None and not self._owner.done()

    def _make_client(self, stack: AsyncExitStack) -> Client:
        ext = [advertise(EXTENSION_ID, {"mimeTypes": [RESOURCE_MIME_TYPE]})]
        cfg = self.config
        server: Any
        if cfg.transport == "stdio":
            server = StdioServerParameters(
                command=cfg.command or "", args=list(cfg.args), env=dict(cfg.env or {}) or None
            )
        elif cfg.headers or cfg.auth is not None:
            from mcp.shared._httpx_utils import create_mcp_http_client

            http = create_mcp_http_client(headers=dict(cfg.headers or {}), auth=cfg.auth)
            server = streamable_http_client(cfg.url or "", http_client=http)
            stack.push_async_callback(http.aclose)
        else:
            server = cfg.url
        return Client(
            server,
            client_info=CLIENT_INFO,
            extensions=ext,
            read_timeout_seconds=self.timeout,
            cache=None,
        )

    async def _own(self, ready: asyncio.Future[None], closing: asyncio.Event) -> None:
        try:
            async with AsyncExitStack() as stack:
                client = self._make_client(stack)
                async with client:
                    self._client = client
                    ready.set_result(None)
                    await closing.wait()
        except BaseException as e:  # noqa: BLE001 - surfaced to the waiter
            if not ready.done():
                ready.set_exception(e)
            elif not isinstance(e, asyncio.CancelledError):
                logger.warning("MCP connection %r ended: %r", self.name, _unwrap(e))
        finally:
            self._client = None

    async def connect(self) -> Client:
        """Connect if necessary and return the live client."""
        if self._closed:
            raise ServerUnavailableError(f"Connection to {self.name!r} is closed")
        async with self._lock:
            if self.connected:
                assert self._client is not None
                return self._client
            await self._stop_owner()
            loop = asyncio.get_running_loop()
            ready: asyncio.Future[None] = loop.create_future()
            closing = asyncio.Event()
            self._closing = closing
            self._owner = loop.create_task(self._own(ready, closing), name=f"mcp:{self.name}")
            try:
                await asyncio.wait_for(asyncio.shield(ready), self.timeout)
            except TimeoutError:
                await self._stop_owner()
                raise RequestTimeoutError(f"Timed out connecting to MCP server {self.name!r}") from None
            except BaseException as e:
                await self._stop_owner()
                leaf = _unwrap(e)
                raise ServerUnavailableError(
                    f"Could not connect to MCP server {self.name!r}: {type(leaf).__name__}: {leaf}"
                ) from None
            assert self._client is not None
            return self._client

    async def _stop_owner(self) -> None:
        owner, self._owner = self._owner, None
        if self._closing is not None:
            self._closing.set()
        if owner is not None and not owner.done():
            try:
                await asyncio.wait_for(owner, 5)
            except BaseException:  # noqa: BLE001
                owner.cancel()
        self._client = None

    async def _request(self, what: str, coro_fn: Any, timeout: float | None) -> Any:
        client = await self.connect()
        try:
            return await asyncio.wait_for(coro_fn(client), timeout or self.timeout)
        except TimeoutError:
            raise RequestTimeoutError(f"{what} timed out after {timeout or self.timeout:g}s") from None
        except MCPHostError:
            raise
        except MCPError as e:
            raise ToolCallError(f"{what} failed: {e.message} (code {e.code})") from None
        except Exception as e:  # noqa: BLE001
            leaf = _unwrap(e)
            if not self.connected:
                raise ServerUnavailableError(
                    f"MCP server {self.name!r} became unavailable during {what}: {leaf}"
                ) from None
            raise ToolCallError(f"{what} failed: {type(leaf).__name__}: {leaf}") from None

    async def list_tools(self, *, refresh: bool = False) -> list[Tool]:
        """Return all tools (following pagination); cached until ``refresh``."""
        if self._tools is not None and not refresh:
            return list(self._tools.values())

        async def run(client: Client) -> list[Tool]:
            tools: list[Tool] = []
            cursor: str | None = None
            for _ in range(100):
                page = await client.list_tools(cursor=cursor)
                tools.extend(page.tools)
                cursor = page.next_cursor
                if not cursor:
                    break
            return tools

        tools = await self._request("tools/list", run, None)
        self._tools = {t.name: t for t in tools}
        return tools

    async def get_tool(self, name: str) -> Tool | None:
        tools = await self.list_tools()
        found = next((t for t in tools if t.name == name), None)
        if found is None:
            tools = await self.list_tools(refresh=True)
            found = next((t for t in tools if t.name == name), None)
        return found

    async def call_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None, *, timeout: float | None = None
    ) -> CallToolResult:
        return await self._request(
            f"tools/call {name!r}",
            lambda c: c.call_tool(name, dict(arguments or {})),
            timeout,
        )

    async def read_resource(self, uri: str, *, timeout: float | None = None) -> ReadResourceResult:
        return await self._request(f"resources/read {uri!r}", lambda c: c.read_resource(uri), timeout)

    async def close(self) -> None:
        self._closed = True
        await self._stop_owner()
