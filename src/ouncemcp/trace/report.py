"""Trace reports for ``ouncemcp trace`` (spec DX-5, proposal 0018).

Reads traces written by ``JsonlSink`` and renders them as a terminal summary or as an
A2UI surface (proposal 0020): summary figures, each tool's calls, errors by category,
truncations, result tokens and latency, and every call with its outcome, tokens
against budget, attempts, truncation decisions and error code. ``render_html`` puts
the surface in one HTML file that opens offline; ``a2ui_messages`` gives the A2UI
messages for any host that renders A2UI.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ouncemcp.trace import CallTrace
from ouncemcp.trace.metrics import MetricsSink
from ouncemcp.ui.a2ui import Surface, bind, render_page

_FIELDS = {f.name for f in dataclasses.fields(CallTrace)}


def load_traces(path: str | Path) -> list[CallTrace]:
    """Read a JSONL trace file. Lines that are not traces are skipped."""
    traces: list[CallTrace] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            traces.append(CallTrace(**{k: v for k, v in data.items() if k in _FIELDS}))
        except (json.JSONDecodeError, TypeError):
            continue
    return traces


def summarize(traces: Iterable[CallTrace]) -> dict[str, Any]:
    sink = MetricsSink()
    items = list(traces)
    for trace in items:
        sink.emit(trace)
    errors = sum(1 for t in items if t.outcome == "error")
    return {
        "calls": len(items),
        "errors": errors,
        "truncated": sum(1 for t in items if t.truncated),
        "result_tokens": sum(t.result_tokens for t in items),
        "tools": sink.snapshot(),
        "tokenizers": sorted({t.tokenizer for t in items if t.tokenizer}),
    }


def render_text(traces: Sequence[CallTrace]) -> str:
    s = summarize(traces)
    tokenizers = ", ".join(s["tokenizers"]) or "no tokenizer"
    headline = (
        f"{s['calls']} calls, {s['errors']} errors, {s['truncated']} truncated, "
        f"{s['result_tokens']:,} result tokens ({tokenizers})"
    )
    header = (
        f"{'Tool':<32} {'Calls':>6} {'Errors':>7} {'Trunc':>6} {'Tokens avg':>11} "
        f"{'p50 ms':>8} {'p95 ms':>8}"
    )
    lines = [headline, "", header]
    for tool, m in s["tools"].items():
        lines.append(
            f"{tool[:32]:<32} {m['calls']:>6} {sum(m['errors'].values()):>7} "
            f"{m['truncated']:>6} {m['result_tokens_avg']:>11} {m['latency_ms_p50']:>8.1f} "
            f"{m['latency_ms_p95']:>8.1f}"
        )
    return "\n".join(lines)


MAX_CALLS = 2000  # calls listed in a report; the summary always covers every call


def _call_row(t: CallTrace) -> dict[str, str]:
    when = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t.started_at))
        + f".{int(t.started_at % 1 * 1000):03d}"
    )
    budget = f" / {t.budget:,}" if t.budget else ""
    facts = [when, f"{t.result_tokens:,}{budget} tokens", f"{t.duration_ms:.1f} ms"]
    if t.attempts != 1:
        facts.append(f"{t.attempts} attempts")
    notes = []
    if t.replayed:
        notes.append("replayed")
    if t.resolved_by:
        notes.append(f"resolved by {t.resolved_by}")
    if t.parent:
        notes.append(f"via {t.parent}")
    removed = ""
    if t.truncated:
        removed = "Truncated: " + (
            json.dumps(t.truncation, separators=(", ", ": "), default=str)
            if t.truncation
            else "yes"
        )
    return {
        "tool": t.tool,
        "outcome": t.outcome,
        "facts": " · ".join(facts),
        "error": f"{t.error_category}: {t.error_code}" if t.error_code else "",
        "notes": ", ".join(notes),
        "removed": removed,
    }


def _tool_row(tool: str, m: dict[str, Any]) -> dict[str, Any]:
    errors = ", ".join(f"{k} {v}" for k, v in sorted(m["errors"].items())) or "no errors"
    return {
        "tool": tool,
        "counts": f"{m['calls']:,} calls · {errors} · {m['truncated']:,} truncated",
        "tokens": f"Result tokens avg {m['result_tokens_avg']:,.0f}, max {m['result_tokens_max']:,}",
        "latency": f"Latency p50 {m['latency_ms_p50']:.1f} ms, p95 {m['latency_ms_p95']:.1f} ms",
    }


def _stat(surface: Surface, key: str, label: str, value: str) -> str:
    surface.text(f"stat_{key}_label", label, "caption")
    surface.text(f"stat_{key}_value", value, "h2")
    surface.column(f"stat_{key}_col", [f"stat_{key}_label", f"stat_{key}_value"])
    return surface.card(f"stat_{key}", f"stat_{key}_col", weight=1)


def a2ui_surface(
    traces: Sequence[CallTrace], *, title: str = "OunceMCP trace"
) -> tuple[Surface, dict[str, dict[str, Any]]]:
    """The trace report as an A2UI surface, and the views its buttons switch between."""
    s = summarize(traces)
    shown = list(traces)[-MAX_CALLS:]
    rows = [_call_row(t) for t in shown]
    tokenizers = ", ".join(s["tokenizers"]) or "an unknown tokenizer"
    limit = f" The last {MAX_CALLS:,} calls are listed." if len(traces) > MAX_CALLS else ""

    def view(label: str, keep: Any) -> dict[str, Any]:
        picked = [r for t, r in zip(shown, rows, strict=True) if keep(t)]
        return {"/calls": picked, "/calls_label": f"{label}: {len(picked):,} calls"}

    views = {
        "all": view("All calls", lambda t: True),
        "errors": view("Errors", lambda t: t.outcome == "error"),
        "truncated": view("Truncated", lambda t: t.truncated),
        "replayed": view("Replayed", lambda t: t.replayed),
    }
    for tool in s["tools"]:
        views[f"tool:{tool}"] = view(tool, lambda t, tool=tool: t.tool == tool)

    ui = Surface("trace", theme={"primaryColor": "#0969da", "agentDisplayName": "OunceMCP"})
    ui.data = {
        "tools": [_tool_row(tool, m) for tool, m in s["tools"].items()],
        **views["all"],
    }
    ui.text("title", title, "h1")
    ui.text(
        "about",
        f"Result tokens counted with {tokenizers}. Principals are digests; arguments, results "
        f"and error messages are not recorded unless the tracer was set to record arguments.{limit}",
        "caption",
    )
    stats = [
        _stat(ui, "calls", "Calls", f"{s['calls']:,}"),
        _stat(ui, "errors", "Errors", f"{s['errors']:,}"),
        _stat(ui, "truncated", "Truncated", f"{s['truncated']:,}"),
        _stat(ui, "tokens", "Result tokens", f"{s['result_tokens']:,}"),
    ]
    ui.row("stats", stats, align="stretch")

    # By tool: one card per tool.
    ui.text("t_name", bind("tool"), "h4")
    ui.text("t_counts", bind("counts"), "caption")
    ui.text("t_tokens", bind("tokens"), "caption")
    ui.text("t_latency", bind("latency"), "caption")
    ui.column("t_col", ["t_name", "t_counts", "t_tokens", "t_latency"])
    ui.card("t_card", "t_col")
    ui.repeat("tools_list", "t_card", "/tools")

    # Calls: filters by outcome and by tool, then one card per call.
    filters = [
        ui.button(f"f_{name}", label, "show", {"view": name})
        for name, label in (
            ("all", "All"),
            ("errors", "Errors"),
            ("truncated", "Truncated"),
            ("replayed", "Replayed"),
        )
    ]
    ui.add("filters", "List", children=filters, direction="horizontal", align="center")
    tool_filters = [
        ui.button(f"f_tool_{i}", tool, "show", {"view": f"tool:{tool}"}, variant="borderless")
        for i, tool in enumerate(s["tools"])
    ]
    ui.text("tool_filters_label", "By tool", "caption")
    ui.add("tool_filters", "List", children=tool_filters, direction="horizontal", align="center")
    ui.text("calls_label", bind("/calls_label"), "h3")
    ui.text("c_tool", bind("tool"), "h4", weight=1)
    ui.text("c_outcome", bind("outcome"))
    ui.row("c_head", ["c_tool", "c_outcome"])
    ui.text("c_facts", bind("facts"), "caption")
    ui.text("c_error", bind("error"), "caption")
    ui.text("c_notes", bind("notes"), "caption")
    ui.text("c_removed", bind("removed"), "caption")
    ui.column("c_col", ["c_head", "c_facts", "c_error", "c_notes", "c_removed"])
    ui.card("c_card", "c_col")
    ui.repeat("calls_list", "c_card", "/calls")
    ui.column(
        "calls_tab", ["filters", "tool_filters_label", "tool_filters", "calls_label", "calls_list"]
    )

    ui.tabs("tabs", [("By tool", "tools_list"), ("Calls", "calls_tab")])
    ui.column("root", ["title", "about", "stats", "tabs"])
    return ui, views


def a2ui_messages(
    traces: Sequence[CallTrace], *, title: str = "OunceMCP trace"
) -> list[dict[str, Any]]:
    return a2ui_surface(traces, title=title)[0].messages()


def render_html(traces: Sequence[CallTrace], *, title: str = "OunceMCP trace") -> str:
    """One HTML file that renders the trace report with the bundled A2UI renderer."""
    surface, views = a2ui_surface(traces, title=title)
    return render_page(title, surface.messages(), fallback=render_text(traces), views=views)
