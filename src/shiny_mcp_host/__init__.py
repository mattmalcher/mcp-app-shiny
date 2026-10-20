"""Render standards-compliant MCP Apps inside Python Shiny applications.

Names are imported lazily, so ``shiny_mcp_host.view`` (for MCP server authors)
can be used without importing Shiny.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ._version import __version__

_EXPORTS = {
    "MCPHost": ".host",
    "ToolInfo": ".host",
    "RenderResult": ".host",
    "ModelContextEvent": ".host",
    "AppMessageEvent": ".host",
    "HostPolicy": ".policy",
    "mcp_widget": ".components",
    "html_dependency": ".components",
    "MCPHostError": ".errors",
    "PolicyError": ".errors",
    "RequestTimeoutError": ".errors",
    "ResourceError": ".errors",
    "SandboxConfigError": ".errors",
    "ServerUnavailableError": ".errors",
    "ToolCallError": ".errors",
    "EXTENSION_ID": ".protocol",
    "PROTOCOL_VERSION": ".protocol",
    "RESOURCE_MIME_TYPE": ".protocol",
    "get_tool_ui_resource_uri": ".protocol",
}

__all__ = (
    "__version__",
    "MCPHost",
    "ToolInfo",
    "RenderResult",
    "ModelContextEvent",
    "AppMessageEvent",
    "HostPolicy",
    "mcp_widget",
    "html_dependency",
    "MCPHostError",
    "PolicyError",
    "RequestTimeoutError",
    "ResourceError",
    "SandboxConfigError",
    "ServerUnavailableError",
    "ToolCallError",
    "EXTENSION_ID",
    "PROTOCOL_VERSION",
    "RESOURCE_MIME_TYPE",
    "get_tool_ui_resource_uri",
)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


if TYPE_CHECKING:
    from .components import html_dependency, mcp_widget
    from .errors import (
        MCPHostError,
        PolicyError,
        RequestTimeoutError,
        ResourceError,
        SandboxConfigError,
        ServerUnavailableError,
        ToolCallError,
    )
    from .host import AppMessageEvent, MCPHost, ModelContextEvent, RenderResult, ToolInfo
    from .policy import HostPolicy
    from .protocol import EXTENSION_ID, PROTOCOL_VERSION, RESOURCE_MIME_TYPE, get_tool_ui_resource_uri
