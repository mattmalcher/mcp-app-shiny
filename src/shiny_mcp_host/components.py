"""Shiny UI: the widget mount point and the bundled browser dependency."""

from __future__ import annotations

from pathlib import Path

from htmltools import HTMLDependency, Tag, tags
from shiny.module import resolve_id

from ._version import __version__

__all__ = ("mcp_widget", "html_dependency", "DEFAULT_HOST_ID")

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_HOST_ID = "mcp"


def html_dependency() -> HTMLDependency:
    """The packaged JS/CSS host adapter. Attached automatically by :func:`mcp_widget`."""
    return HTMLDependency(
        name="shiny-mcp-host",
        version=__version__,
        source={"subdir": str(STATIC_DIR)},
        script={"src": "shiny-mcp-host.js"},
        stylesheet={"href": "shiny-mcp-host.css"},
        all_files=False,
    )


def _css_size(value: int | str) -> str:
    return f"{value}px" if isinstance(value, int) else str(value)


def mcp_widget(id: str, *, host: str = DEFAULT_HOST_ID, height: int | str = 360) -> Tag:
    """A mount point for an MCP App View.

    The element is a ``<shiny-mcp-widget>`` custom element: the browser adapter
    starts the View only once the element is connected to the document (so it
    works in static pages, dynamic UI and ``ui.Chat`` messages alike), and
    tears it down when the element is removed.

    Parameters
    ----------
    id
        Widget id; pass the same id as ``target`` to
        :meth:`MCPHost.call_and_render`. Namespaced inside Shiny modules.
    host
        Id of the :class:`MCPHost` that drives this widget.
    height
        Initial height of the View (the View may request a different height).
    """
    return widget_tag(resolve_id(id), resolve_id(host), height)


def widget_tag(widget_id: str, host_id: str, height: int | str = 360) -> Tag:
    """Build the mount point for already-resolved (namespaced) ids."""
    return Tag(
        "shiny-mcp-widget",
        html_dependency(),
        tags.div("Waiting for an MCP App…", class_="shiny-mcp-widget-placeholder"),
        {
            "data-widget-id": widget_id,
            "data-host-id": host_id,
            "style": f"--shiny-mcp-initial-height: {_css_size(height)}",
        },
        class_="shiny-mcp-widget",
    )
