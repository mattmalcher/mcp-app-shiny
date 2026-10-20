"""MCP Apps metadata helpers and the validated Shiny RPC envelope.

The metadata helpers are faithful ports of the official TypeScript helpers in
``@modelcontextprotocol/ext-apps/app-bridge`` (``getToolUiResourceUri`` and
friends); the Python MCP SDK ships server-side Apps support only.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .errors import MCPHostError, ResourceError

__all__ = (
    "EXTENSION_ID",
    "RESOURCE_MIME_TYPE",
    "RESOURCE_URI_META_KEY",
    "PROTOCOL_VERSION",
    "Envelope",
    "get_tool_ui_resource_uri",
    "parse_envelope",
    "extract_ui_html",
)

#: MCP Apps extension identifier advertised in client capabilities.
EXTENSION_ID = "io.modelcontextprotocol/ui"
#: MIME type of an MCP App HTML resource.
RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"
#: Deprecated flat tool metadata key (still read for compatibility).
RESOURCE_URI_META_KEY = "ui/resourceUri"
#: Stable MCP Apps specification version implemented.
PROTOCOL_VERSION = "2026-01-26"

#: Version of the Shiny <-> browser envelope defined in docs/protocol-contract.md.
ENVELOPE_VERSION = 1

JS_KINDS = (
    "mount",
    "unmount",
    "initialized",
    "tools/call",
    "resources/read",
    "model-context",
    "message",
    "open-link",
    "log",
    "view-error",
)
JsKind = Literal[
    "mount",
    "unmount",
    "initialized",
    "tools/call",
    "resources/read",
    "model-context",
    "message",
    "open-link",
    "log",
    "view-error",
]

_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


def _tool_meta(tool: Any) -> Mapping[str, Any] | None:
    meta = getattr(tool, "meta", None)
    if meta is None and isinstance(tool, Mapping):
        meta = tool.get("_meta")
    return meta if isinstance(meta, Mapping) else None


def get_tool_ui_resource_uri(tool: Any) -> str | None:
    """Return the ``ui://`` resource URI linked to ``tool``, or ``None``.

    Reads ``_meta.ui.resourceUri`` and falls back to the deprecated flat
    ``_meta["ui/resourceUri"]``. Raises :class:`ResourceError` for a present
    but invalid (non-``ui://``) value, matching the official helper.
    """
    meta = _tool_meta(tool)
    if meta is None:
        return None
    ui = meta.get("ui")
    uri: Any = ui.get("resourceUri") if isinstance(ui, Mapping) else None
    if uri is None:
        uri = meta.get(RESOURCE_URI_META_KEY)
    if uri is None:
        return None
    if isinstance(uri, str) and uri.startswith("ui://"):
        return uri
    raise ResourceError(f"Invalid UI resource URI: {uri!r}")


def extract_ui_html(result: Any, *, expected_uri: str, max_bytes: int) -> tuple[str, Mapping[str, Any]]:
    """Validate a ``resources/read`` result for an MCP App and return ``(html, ui_meta)``.

    Enforces: exactly one content item, matching URI (no cross-contamination),
    the MCP App MIME type, text or valid base64 blob, and a size bound.
    """
    contents = getattr(result, "contents", None)
    if not isinstance(contents, (list, tuple)) or len(contents) != 1:
        count = len(contents) if isinstance(contents, (list, tuple)) else "no"
        raise ResourceError(f"Expected exactly one resource content item, got {count}")
    content = contents[0]
    uri = str(getattr(content, "uri", ""))
    if uri != expected_uri:
        raise ResourceError(f"Resource URI mismatch: requested {expected_uri!r}, got {uri!r}")
    mime = getattr(content, "mime_type", None)
    if mime != RESOURCE_MIME_TYPE:
        raise ResourceError(f"Unsupported MIME type: {mime!r}")
    text = getattr(content, "text", None)
    if isinstance(text, str):
        if len(text.encode("utf-8")) > max_bytes:
            raise ResourceError(f"UI resource exceeds {max_bytes} bytes")
        html = text
    else:
        blob = getattr(content, "blob", None)
        if not isinstance(blob, str):
            raise ResourceError("Resource has neither text nor blob content")
        if len(blob) > (max_bytes * 4) // 3 + 4:
            raise ResourceError(f"UI resource exceeds {max_bytes} bytes")
        try:
            html = base64.b64decode(blob, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            raise ResourceError(f"Invalid base64 resource blob: {e}") from None
    if not html.strip():
        raise ResourceError("UI resource is empty")
    meta = getattr(content, "meta", None)
    ui = meta.get("ui") if isinstance(meta, Mapping) else None
    return html, (ui if isinstance(ui, Mapping) else {})


@dataclass(frozen=True)
class Envelope:
    """A validated browser -> Python message."""

    kind: JsKind
    widget_id: str
    mount_id: str
    request_id: str | None
    render_id: int | None
    payload: dict[str, Any]


def parse_envelope(raw: object) -> Envelope:
    """Validate an untrusted envelope from the browser. Raises :class:`MCPHostError`."""
    if not isinstance(raw, Mapping):
        raise MCPHostError("Envelope must be an object")
    if raw.get("v") != ENVELOPE_VERSION:
        raise MCPHostError(f"Unsupported envelope version: {raw.get('v')!r}")
    kind = raw.get("kind")
    if kind not in JS_KINDS:
        raise MCPHostError(f"Unknown envelope kind: {kind!r}")
    widget_id = raw.get("widget_id")
    mount_id = raw.get("mount_id")
    if not isinstance(widget_id, str) or not _ID.match(widget_id):
        raise MCPHostError("Invalid widget_id")
    if not isinstance(mount_id, str) or not _ID.match(mount_id):
        raise MCPHostError("Invalid mount_id")
    request_id = raw.get("request_id")
    if request_id is not None and (not isinstance(request_id, str) or not _ID.match(request_id)):
        raise MCPHostError("Invalid request_id")
    render_id = raw.get("render_id")
    if render_id is not None and (not isinstance(render_id, int) or isinstance(render_id, bool)):
        raise MCPHostError("Invalid render_id")
    payload = raw.get("payload", {})
    if not isinstance(payload, Mapping):
        raise MCPHostError("payload must be an object")
    return Envelope(
        kind=kind,  # type: ignore[arg-type]
        widget_id=widget_id,
        mount_id=mount_id,
        request_id=request_id,
        render_id=render_id,
        payload=_thaw(payload),
    )


def _thaw(value: Any) -> Any:
    """Shiny decodes JSON arrays as tuples; convert back to plain JSON types."""
    if isinstance(value, Mapping):
        return {str(k): _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


def to_wire(model: Any) -> Any:
    """Serialise an MCP SDK pydantic model to its JSON wire form for the View."""
    if hasattr(model, "model_dump"):
        data = model.model_dump(mode="json", by_alias=True, exclude_none=True)
    else:
        data = model
    if isinstance(data, dict):
        # SDK-internal fields from newer protocol revisions; not part of the
        # 2025-11-25 result shapes the View's App SDK validates against.
        for key in ("resultType", "ttlMs", "cacheScope"):
            data.pop(key, None)
    return data
