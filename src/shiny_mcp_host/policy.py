"""Host policy: which View-originated requests are authorised, and resource limits.

Everything here is enforced server-side (in Python), so a malicious View cannot
bypass it by tampering with the browser-side bridge.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .errors import PolicyError

__all__ = ("HostPolicy", "sanitize_csp", "sanitize_permissions", "validate_open_link")

#: CSP keys defined by the MCP Apps spec (``McpUiResourceCsp``).
CSP_KEYS = ("connectDomains", "resourceDomains", "frameDomains", "baseUriDomains")

#: Permission keys defined by the MCP Apps spec (``McpUiResourcePermissions``).
PERMISSION_KEYS = ("camera", "microphone", "geolocation", "clipboardWrite")

# scheme://host[:port] with an optional leading "*." wildcard label; no path,
# quotes, semicolons or whitespace (which could inject CSP directives).
_CSP_SOURCE = re.compile(
    r"^(https|wss|http|ws)://(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*(:\d{1,5})?$"
)
_LOOPBACK = {"localhost", "127.0.0.1", "[::1]"}


def _is_valid_csp_source(source: object) -> bool:
    if not isinstance(source, str) or len(source) > 253 or not _CSP_SOURCE.match(source):
        return False
    parts = urlsplit(source)
    # Plain-text schemes are only acceptable for loopback development servers.
    if parts.scheme in ("http", "ws") and (parts.hostname or "") not in _LOOPBACK:
        return False
    return True


@dataclass(frozen=True)
class HostPolicy:
    """Authorisation and limits applied by :class:`~shiny_mcp_host.MCPHost`.

    Parameters
    ----------
    app_tools
        Tools a View may call through the host, as ``{server_name: {tool, ...}}``.
        When ``None`` (the default), a View may call only the tool that produced
        it plus tools on the *same* server whose ``_meta.ui.visibility``
        explicitly includes ``"app"``. In every case the spec rules still apply:
        the tool must exist on the View's own server and its visibility must
        include ``"app"``. Cross-server calls are always rejected.
    allowed_csp_domains
        External origins a View may load resources from / connect to. Domains a
        resource declares in ``_meta.ui.csp`` that are not in this list are
        dropped (the host may restrict, never loosen, the declared CSP).
    trust_declared_csp
        Accept every well-formed domain the resource declares instead of
        requiring it to appear in ``allowed_csp_domains``.
    allowed_permissions
        Iframe permissions (``camera``, ``microphone``, ``geolocation``,
        ``clipboardWrite``) that may be granted when a resource requests them.
    allow_open_links
        Whether ``ui/open-link`` is honoured (http/https URLs only).
    allow_resource_read
        Whether a View may ``resources/read`` from its own server.
    view_same_origin
        Add ``allow-same-origin`` to the inner View iframe. Off by default, so
        the View runs in an opaque origin; some third-party Views (e.g. ones
        using ``localStorage``) need it. Even when enabled, the View shares
        the *sandbox* origin, never the Shiny origin.
    max_resource_bytes, max_tool_payload_bytes
        Upper bounds for UI resource HTML and View-originated tool arguments.
    max_pending_per_widget
        Concurrent View-originated requests allowed per widget.
    request_timeout
        Seconds before an MCP request is abandoned with a timeout error.
    """

    app_tools: Mapping[str, Collection[str]] | None = None
    allowed_csp_domains: Collection[str] = ()
    trust_declared_csp: bool = False
    allowed_permissions: Collection[str] = ()
    allow_open_links: bool = True
    allow_resource_read: bool = True
    view_same_origin: bool = False
    max_resource_bytes: int = 5 * 1024 * 1024
    max_tool_payload_bytes: int = 256 * 1024
    max_pending_per_widget: int = 16
    request_timeout: float = 30.0

    def __post_init__(self) -> None:
        bad = [d for d in self.allowed_csp_domains if not _is_valid_csp_source(d)]
        if bad:
            raise ValueError(f"Invalid CSP domain(s) in allowed_csp_domains: {bad!r}")
        unknown = set(self.allowed_permissions) - set(PERMISSION_KEYS)
        if unknown:
            raise ValueError(f"Unknown permission(s): {sorted(unknown)!r}")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")

    def check_app_tool_call(
        self,
        *,
        view_server: str,
        target_server: str,
        tool_name: str,
        origin_tool: str,
        tool: Any | None,
    ) -> None:
        """Raise :class:`PolicyError` unless a View may call ``tool_name``.

        ``tool`` is the tool definition from the server's listing (or ``None``).
        """
        if target_server != view_server:
            raise PolicyError("Cross-server tool calls are not allowed")
        if tool is None:
            raise PolicyError(f"Unknown tool: {tool_name}")
        visibility = tool_visibility(tool)
        if "app" not in visibility:
            raise PolicyError(f"Tool {tool_name!r} is not visible to apps")
        if self.app_tools is not None:
            allowed = self.app_tools.get(view_server, ())
            if tool_name not in allowed:
                raise PolicyError(f"Tool {tool_name!r} is not in the app tool allowlist")
            return
        explicit = _ui_meta(tool).get("visibility")
        if tool_name == origin_tool:
            return
        if isinstance(explicit, list) and "app" in explicit:
            return
        raise PolicyError(
            f"Tool {tool_name!r} is not authorised for this View "
            "(add it to HostPolicy.app_tools to allow it)"
        )


def _ui_meta(tool: Any) -> dict[str, Any]:
    meta = getattr(tool, "meta", None)
    if meta is None and isinstance(tool, Mapping):
        meta = tool.get("_meta")
    ui = meta.get("ui") if isinstance(meta, Mapping) else None
    return dict(ui) if isinstance(ui, Mapping) else {}


def tool_visibility(tool: Any) -> list[str]:
    """Return a tool's ``_meta.ui.visibility``, defaulting to ``["model", "app"]``."""
    visibility = _ui_meta(tool).get("visibility")
    if isinstance(visibility, list) and all(isinstance(v, str) for v in visibility):
        return list(visibility)
    return ["model", "app"]


def sanitize_csp(csp: object, policy: HostPolicy) -> tuple[dict[str, list[str]], list[str]]:
    """Filter a resource's declared ``_meta.ui.csp`` through ``policy``.

    Returns ``(csp, rejected)``: the CSP to apply (only well-formed, approved
    sources) and the declared sources that were dropped.
    """
    result: dict[str, list[str]] = {}
    rejected: list[str] = []
    if not isinstance(csp, Mapping):
        return result, rejected
    approved = set(policy.allowed_csp_domains)
    for key in CSP_KEYS:
        values = csp.get(key)
        if not isinstance(values, list):
            continue
        kept: list[str] = []
        for value in values[:64]:
            if _is_valid_csp_source(value) and (policy.trust_declared_csp or value in approved):
                kept.append(value)
            else:
                rejected.append(str(value)[:200])
        if kept:
            result[key] = kept
    return result, rejected


def sanitize_permissions(permissions: object, policy: HostPolicy) -> dict[str, dict[str, Any]]:
    """Keep only requested permissions that the policy allows."""
    if not isinstance(permissions, Mapping):
        return {}
    return {
        key: {}
        for key in PERMISSION_KEYS
        if key in permissions and key in policy.allowed_permissions
    }


def validate_open_link(url: object) -> str:
    """Return ``url`` if it is an absolute http(s) URL, else raise :class:`PolicyError`."""
    if not isinstance(url, str) or len(url) > 4096:
        raise PolicyError("Invalid URL")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PolicyError(f"Refusing to open URL with scheme {parts.scheme!r}")
    return url.strip()
