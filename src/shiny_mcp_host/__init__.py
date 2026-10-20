"""Render standards-compliant MCP Apps inside Python Shiny applications."""

from ._version import __version__
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

__all__ = (
    "__version__",
    "MCPHost",
    "HostPolicy",
    "mcp_widget",
    "html_dependency",
    "ToolInfo",
    "RenderResult",
    "ModelContextEvent",
    "AppMessageEvent",
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
