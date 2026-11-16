"""Per-tool metrics (spec 9.4, proposal 0017).

``MetricsSink`` is a trace sink that keeps, for every tool: calls, errors by category,
latency (p50, p95, max over the last 1,000 calls), result tokens (total, average,
max) and truncations. ``snapshot()`` returns them as a dict, and ``prometheus()`` in the
Prometheus text format. ``OunceServer(metrics=True)`` attaches one and serves it at
``/metrics`` on HTTP transports.
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from ouncemcp.trace import CallTrace

WINDOW = 1000


MAX_TOOLS = 2_000
OVERFLOW = "(other)"


@dataclass
class _ToolStats:
    calls: int = 0
    truncated: int = 0
    tokens: int = 0
    max_tokens: int = 0
    errors: Counter[str] = field(default_factory=Counter)
    latency: deque[float] = field(default_factory=lambda: deque(maxlen=WINDOW))


def _escape(value: str) -> str:
    """Escape a Prometheus label value: backslash, double quote and newline."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


class MetricsSink:
    """Aggregates traces into per-tool metrics. Thread-safe."""

    def __init__(self, *, max_tools: int = MAX_TOOLS) -> None:
        self._stats: dict[str, _ToolStats] = defaultdict(_ToolStats)
        self._lock = threading.Lock()
        self._max_tools = max_tools

    def emit(self, trace: CallTrace) -> None:
        with self._lock:
            name = trace.tool
            if name not in self._stats and len(self._stats) >= self._max_tools:
                name = OVERFLOW  # bounded memory and label count, whatever is called
            stats = self._stats[name]
            stats.calls += 1
            stats.tokens += trace.result_tokens
            stats.max_tokens = max(stats.max_tokens, trace.result_tokens)
            stats.truncated += int(trace.truncated)
            stats.latency.append(trace.duration_ms)
            if trace.outcome == "error":
                stats.errors[trace.error_category or "unknown"] += 1

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            out = {}
            for tool, s in sorted(self._stats.items()):
                latency = list(s.latency)
                out[tool] = {
                    "calls": s.calls,
                    "errors": dict(s.errors),
                    "truncated": s.truncated,
                    "result_tokens_total": s.tokens,
                    "result_tokens_avg": round(s.tokens / s.calls, 1) if s.calls else 0,
                    "result_tokens_max": s.max_tokens,
                    "latency_ms_p50": round(_quantile(latency, 0.5), 3),
                    "latency_ms_p95": round(_quantile(latency, 0.95), 3),
                    "latency_ms_max": round(max(latency, default=0.0), 3),
                }
            return out

    def prometheus(self) -> str:
        lines = [
            "# HELP ouncemcp_tool_calls_total Tool calls.",
            "# TYPE ouncemcp_tool_calls_total counter",
        ]
        snap = self.snapshot()

        def label(tool: str, **extra: str) -> str:
            pairs = {"tool": tool, **extra}
            return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in pairs.items()) + "}"

        for tool, m in snap.items():
            lines.append(f"ouncemcp_tool_calls_total{label(tool)} {m['calls']}")
        lines += [
            "# HELP ouncemcp_tool_errors_total Tool calls that failed, by category.",
            "# TYPE ouncemcp_tool_errors_total counter",
        ]
        for tool, m in snap.items():
            for category, n in sorted(m["errors"].items()):
                lines.append(f"ouncemcp_tool_errors_total{label(tool, category=category)} {n}")
        for name, key, help_text in (
            ("ouncemcp_tool_truncations_total", "truncated", "Results trimmed to fit the budget."),
            ("ouncemcp_tool_result_tokens_total", "result_tokens_total", "Result tokens sent."),
        ):
            lines += [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
            lines += [f"{name}{label(tool)} {m[key]}" for tool, m in snap.items()]
        lines += [
            "# HELP ouncemcp_tool_latency_ms Server time per call over the last 1000 calls.",
            "# TYPE ouncemcp_tool_latency_ms gauge",
        ]
        for tool, m in snap.items():
            for q, key in (("0.5", "latency_ms_p50"), ("0.95", "latency_ms_p95")):
                lines.append(f"ouncemcp_tool_latency_ms{label(tool, quantile=q)} {m[key]}")
        return "\n".join(lines) + "\n"
