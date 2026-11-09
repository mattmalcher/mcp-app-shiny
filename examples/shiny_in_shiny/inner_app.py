"""The inner Shiny app: a scatter plot of a toy dataset, served as an MCP App View.

Its controls start from the tool's arguments, the model can move them later
(the ``set_plot_controls`` tool), and moving them by hand shares the new view
with the host as model context.
"""

from __future__ import annotations

import csv
import math
import os
from pathlib import Path
from typing import Any

from htmltools import HTML
from shiny import App, reactive, render, ui
from shiny_view import view

DATA = Path(__file__).parent / "data"


def _load(name: str) -> list[dict[str, Any]]:
    rows = []
    with open(DATA / f"{name}.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({k: _num(v) for k, v in r.items()})
    return rows


def _num(v: str) -> Any:
    try:
        return float(v)
    except ValueError:
        return v or None


DATASETS: dict[str, dict[str, Any]] = {
    "iris": {
        "rows": _load("iris"),
        "numeric": ["sepal_length", "sepal_width", "petal_length", "petal_width"],
        "groups": ["species"],
        "default": ("petal_length", "petal_width", "species"),
    },
    "cars": {
        "rows": _load("cars"),
        "numeric": ["mpg", "horsepower", "weight", "displacement", "acceleration", "year"],
        "groups": ["origin", "cylinders"],
        "default": ("horsepower", "mpg", "origin"),
    },
}
NONE = "none"
PALETTE = ["#2f6db5", "#d1495b", "#2a9d8f", "#e9a03b", "#7b5ea7", "#6c757d"]


def valid_controls(controls: dict[str, Any]) -> dict[str, str]:
    """Fill in and correct a partial {dataset, x, y, color} against the data."""
    dataset = controls.get("dataset") if controls.get("dataset") in DATASETS else "iris"
    spec = DATASETS[dataset]
    dx, dy, dc = spec["default"]
    x = controls.get("x") if controls.get("x") in spec["numeric"] else dx
    y = controls.get("y") if controls.get("y") in spec["numeric"] else dy
    color = controls.get("color") if controls.get("color") in [*spec["groups"], NONE] else dc
    return {"dataset": dataset, "x": x, "y": y, "color": color}


def apply_controls(controls: dict[str, Any]) -> dict[str, str]:
    """Move the inputs in the current session (called on start and by set_plot_controls)."""
    c = valid_controls(controls)
    spec = DATASETS[c["dataset"]]
    ui.update_select("dataset", selected=c["dataset"])
    ui.update_select("x", choices=spec["numeric"], selected=c["x"])
    ui.update_select("y", choices=spec["numeric"], selected=c["y"])
    ui.update_select("color", choices=[*spec["groups"], NONE], selected=c["color"])
    return c


app_ui = ui.page_fillable(
    ui.layout_sidebar(
        ui.sidebar(
            ui.input_select("dataset", "Dataset", list(DATASETS)),
            ui.input_select("x", "X axis", DATASETS["iris"]["numeric"]),
            ui.input_select("y", "Y axis", DATASETS["iris"]["numeric"], selected="sepal_width"),
            ui.input_select("color", "Colour by", [*DATASETS["iris"]["groups"], NONE]),
            ui.input_action_button("ask", "Ask about this plot", class_="btn-sm btn-outline-primary"),
            width=200,
            open="always",
        ),
        ui.output_ui("plot", fill=True),
        ui.output_text("summary"),
        fillable=True,
    ),
    ui.tags.style(
        "body{font-size:14px} .bslib-sidebar-layout>.sidebar{background:#f6f7f9}"
        " #summary{color:#555;font-size:13px}"
    ),
    padding="8px",
    title="Shiny in Shiny",
)


def server(input, output, session):
    mcp = view(session)
    wanted: dict[str, str] = {}

    @reactive.effect
    def _from_tool_input():
        # The tool's arguments set the controls on start; set_plot_controls (the model) sets
        # tool_input again later, partially, so unspecified controls stay where they are.
        requested = mcp.tool_input()
        with reactive.isolate():
            wanted.update(apply_controls({**wanted, **requested}))

    @reactive.effect
    @reactive.event(input.dataset, ignore_init=True)
    def _dataset_picked():
        # A hand-picked dataset: keep the axes if that dataset has them, else use its defaults.
        if input.dataset() != wanted.get("dataset"):
            wanted.update(apply_controls({**wanted, "dataset": input.dataset()}))

    @reactive.calc
    def controls() -> dict[str, str] | None:
        c = {"dataset": input.dataset(), "x": input.x(), "y": input.y(), "color": input.color()}
        return c if valid_controls(c) == c else None  # mid-switch between datasets

    @reactive.calc
    def stats() -> dict[str, Any] | None:
        c = controls()
        if c is None:
            return None
        rows = [r for r in DATASETS[c["dataset"]]["rows"] if r[c["x"]] is not None and r[c["y"]] is not None]
        out = {"n": len(rows), "correlation": round(_corr(rows, c["x"], c["y"]), 3)}
        if c["color"] != NONE:
            groups: dict[str, list] = {}
            for r in rows:
                groups.setdefault(_label(r[c["color"]]), []).append(r)
            out["groups"] = {
                g: {"n": len(rs), f"mean_{c['x']}": _mean(rs, c["x"]), f"mean_{c['y']}": _mean(rs, c["y"])}
                for g, rs in sorted(groups.items())
            }
        return out

    @reactive.effect
    async def _share():
        c, s = controls(), stats()
        if c is None or s is None:
            return
        wanted.update(c)
        text = (
            f"The user is looking at a scatter plot of {c['dataset']}: {c['y']} against {c['x']}"
            + (f", coloured by {c['color']}" if c["color"] != NONE else "")
            + f" ({s['n']} points, correlation {s['correlation']})."
        )
        await mcp.update_model_context({"controls": c, "stats": s, "session": session.id}, text)

    @reactive.effect
    @reactive.event(input.ask)
    async def _ask():
        c = controls()
        if c:
            await mcp.send_message(f"What does the {c['dataset']} plot of {c['y']} against {c['x']} show?")

    @render.ui
    def plot():
        c = controls()
        if c is None:
            return ui.p("…")
        return HTML(_scatter_svg(DATASETS[c["dataset"]]["rows"], c["x"], c["y"], c["color"]))

    @render.text
    def summary():
        s = stats()
        if s is None:
            return ""
        return f"{s['n']} points · correlation {s['correlation']}"


app = App(app_ui, server, debug=bool(os.environ.get("SHINY_VIEW_DEBUG")))


# --------------------------------------------------------------------------- helpers


def _label(v: Any) -> str:
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def _mean(rows: list[dict], key: str) -> float:
    return round(sum(r[key] for r in rows) / len(rows), 2) if rows else float("nan")


def _corr(rows: list[dict], x: str, y: str) -> float:
    n = len(rows)
    if n < 2:
        return float("nan")
    mx, my = sum(r[x] for r in rows) / n, sum(r[y] for r in rows) / n
    sxy = sum((r[x] - mx) * (r[y] - my) for r in rows)
    sxx = sum((r[x] - mx) ** 2 for r in rows)
    syy = sum((r[y] - my) ** 2 for r in rows)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else float("nan")


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    step = 10 ** math.floor(math.log10((hi - lo) / n or 1))
    for m in (1, 2, 5, 10):
        if (hi - lo) / (step * m) <= n:
            step *= m
            break
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def _scatter_svg(rows: list[dict], x: str, y: str, color: str) -> str:
    """A small dependency-free scatter plot (keeps the View free of plotting libraries and images)."""
    rows = [r for r in rows if r[x] is not None and r[y] is not None]
    W, H, L, R, T, B = 560, 360, 52, 120, 12, 40
    xs, ys = [r[x] for r in rows], [r[y] for r in rows]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    px = (x1 - x0) * 0.04 or 1
    py = (y1 - y0) * 0.04 or 1
    x0, x1, y0, y1 = x0 - px, x1 + px, y0 - py, y1 + py

    def sx(v: float) -> float:
        return L + (v - x0) / (x1 - x0) * (W - L - R)

    def sy(v: float) -> float:
        return H - B - (v - y0) / (y1 - y0) * (H - T - B)

    groups = sorted({_label(r[color]) for r in rows}) if color != NONE else []
    col = {g: PALETTE[i % len(PALETTE)] for i, g in enumerate(groups)}
    parts = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" style="max-height:100%;font:11px system-ui,sans-serif"'
        f' role="img" aria-label="{y} against {x}">',
        f'<rect x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}" fill="#fff" stroke="#ddd"/>',
    ]
    for t in _ticks(x0, x1):
        parts.append(
            f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{T}" y2="{H - B}" stroke="#eee"/>'
            f'<text x="{sx(t):.1f}" y="{H - B + 14}" text-anchor="middle" fill="#666">{t:g}</text>'
        )
    for t in _ticks(y0, y1):
        parts.append(
            f'<line x1="{L}" x2="{W - R}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="#eee"/>'
            f'<text x="{L - 6}" y="{sy(t) + 4:.1f}" text-anchor="end" fill="#666">{t:g}</text>'
        )
    parts.append(
        f'<text x="{(L + W - R) / 2}" y="{H - 6}" text-anchor="middle" fill="#333">{x}</text>'
        f'<text transform="translate(14 {(T + H - B) / 2}) rotate(-90)" text-anchor="middle" fill="#333">{y}</text>'
    )
    for r in rows:
        fill = col[_label(r[color])] if groups else PALETTE[0]
        parts.append(
            f'<circle cx="{sx(r[x]):.1f}" cy="{sy(r[y]):.1f}" r="3.2" fill="{fill}" fill-opacity=".7"/>'
        )
    for i, g in enumerate(groups):
        ly = T + 10 + i * 18
        parts.append(
            f'<circle cx="{W - R + 16}" cy="{ly}" r="5" fill="{col[g]}"/>'
            f'<text x="{W - R + 26}" y="{ly + 4}" fill="#333">{g}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)
