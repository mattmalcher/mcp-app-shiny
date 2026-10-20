"""Per-widget state for one :class:`~shiny_mcp_host.MCPHost`.

:class:`WidgetStore` owns every widget record and the two reactive views of
them (lifecycle status and model context), so they are created, ordered and
removed together. It does not talk to the browser or to MCP servers: the host
decides *when* state changes and sends the matching messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from shiny import reactive

if TYPE_CHECKING:
    from .host import ModelContextEvent

#: ``waiting``: nothing rendered yet. ``loading``: a render is in progress in
#: Python. ``rendered``: the outcome was sent to the browser (View not yet
#: initialized, or a text fallback). ``initialized``: the View completed the
#: MCP Apps handshake in the browser. ``error``: Python or the browser reported
#: a failure.
WidgetStatus = Literal["waiting", "loading", "rendered", "initialized", "error"]


@dataclass
class Render:
    """The render message currently shown by a widget (re-sent on remount)."""

    render_id: int
    server: str
    tool: str
    message: dict[str, Any]


@dataclass
class Widget:
    id: str
    mount_id: str | None = None
    render: Render | None = None
    status: WidgetStatus = "waiting"
    status_message: dict[str, Any] | None = None
    #: In-flight View requests: task -> (mount_id, request_id).
    tasks: dict[asyncio.Task[Any], tuple[str, str]] = field(default_factory=dict)
    render_seq: int = 0


class WidgetStore:
    """Widget records plus reactive status and model context, kept consistent."""

    def __init__(self) -> None:
        self._widgets: dict[str, Widget] = {}
        self.statuses: reactive.Value[dict[str, WidgetStatus]] = reactive.Value({})
        self.contexts: reactive.Value[dict[str, ModelContextEvent]] = reactive.Value({})

    def get(self, widget_id: str) -> Widget | None:
        return self._widgets.get(widget_id)

    def get_or_create(self, widget_id: str) -> Widget:
        w = self._widgets.get(widget_id)
        if w is None:
            w = self._widgets[widget_id] = Widget(widget_id)
        return w

    def all(self) -> list[Widget]:
        return list(self._widgets.values())

    # Render ordering: each render to a widget gets a larger token; only the
    # newest token of a widget that is still registered may publish.

    def begin_render(self, widget_id: str) -> tuple[Widget, int]:
        w = self.get_or_create(widget_id)
        w.render_seq += 1
        return w, w.render_seq

    def is_current(self, w: Widget, token: int) -> bool:
        return self._widgets.get(w.id) is w and w.render_seq == token

    # Reactive state. Callers must hold the reactive lock (see host._set_reactive).

    def set_status(self, w: Widget, status: WidgetStatus) -> None:
        w.status = status
        self._update(self.statuses, w.id, status)

    def set_context(self, event: ModelContextEvent) -> None:
        self._update(self.contexts, event.widget_id, None if event.cleared else event)

    def remove(self, widget_id: str) -> tuple[Widget | None, ModelContextEvent | None]:
        """Forget a widget, its status and its model context.

        Returns the removed record and the context it had (if any), so the host
        can cancel its requests and notify model-context callbacks.
        """
        w = self._widgets.pop(widget_id, None)
        with reactive.isolate():
            context = self.contexts().get(widget_id)
        self._update(self.statuses, widget_id, None)
        self._update(self.contexts, widget_id, None)
        return w, context

    def clear(self) -> None:
        self._widgets.clear()
        self.statuses.set({})
        self.contexts.set({})

    @staticmethod
    def _update(value: reactive.Value[dict[str, Any]], key: str, item: Any) -> None:
        with reactive.isolate():
            current = value()
        if item is None and key not in current:
            return
        if item is not None and current.get(key) is item:
            return
        updated = dict(current)
        if item is None:
            updated.pop(key, None)
        else:
            updated[key] = item
        value.set(updated)
