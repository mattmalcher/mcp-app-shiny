"""Exception types raised by shiny-mcp-host."""

from __future__ import annotations

__all__ = (
    "MCPHostError",
    "PolicyError",
    "SandboxConfigError",
    "ServerUnavailableError",
    "ResourceError",
    "ToolCallError",
    "RequestTimeoutError",
)


class MCPHostError(Exception):
    """Base class. ``code`` is a short machine-readable identifier sent to the browser."""

    code = "host_error"


class PolicyError(MCPHostError):
    """A request was rejected by :class:`~shiny_mcp_host.HostPolicy`."""

    code = "policy_rejected"


class SandboxConfigError(MCPHostError):
    """No safe, distinct-origin sandbox is configured. The host fails closed."""

    code = "sandbox_unavailable"


class ServerUnavailableError(MCPHostError):
    """The MCP server could not be reached or the connection failed."""

    code = "server_unavailable"


class ResourceError(MCPHostError):
    """A ``ui://`` resource was missing, malformed, too large or of the wrong type."""

    code = "invalid_resource"


class ToolCallError(MCPHostError):
    """A tool call failed at the protocol level (not an ``isError`` result)."""

    code = "tool_error"


class RequestTimeoutError(MCPHostError):
    """An MCP request did not complete within the policy timeout."""

    code = "timeout"
