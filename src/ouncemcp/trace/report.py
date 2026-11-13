"""Trace reports for ``ouncemcp trace`` (spec DX-5, proposal 0018).

Reads traces written by ``JsonlSink`` and renders them as a terminal summary or as one
HTML file (proposal 0020): summary figures, each tool's calls, errors by category,
truncations, result tokens and latency, and every call with its outcome, tokens
against budget, attempts, truncation decisions and error code, with search and
filters. The page opens offline and can be attached to an issue.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ouncemcp.trace import CallTrace
from ouncemcp.trace.metrics import MetricsSink
from ouncemcp.ui import render_report

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


MAX_CALLS = 5000  # calls listed in a report; the summary always covers every call


def trace_data(traces: Sequence[CallTrace], *, title: str = "OunceMCP trace") -> dict[str, Any]:
    """The data the HTML report shows."""
    s = summarize(traces)
    return {
        "kind": "trace",
        "title": title,
        "summary": {
            k: s[k] for k in ("calls", "errors", "truncated", "result_tokens", "tokenizers")
        },
        "tools": [{"tool": tool, **m} for tool, m in s["tools"].items()],
        "calls": [t.to_dict() for t in list(traces)[-MAX_CALLS:]],
        "calls_total": len(traces),
    }


def render_html(traces: Sequence[CallTrace], *, title: str = "OunceMCP trace") -> str:
    """One HTML file with the trace report; it opens offline."""
    return render_report(trace_data(traces, title=title))
