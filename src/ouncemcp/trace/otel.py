"""OpenTelemetry export for traces (spec DX-5, 9.4; proposal 0018).

``OTelSink`` turns every ``CallTrace`` into one span named ``tools/call <tool>``, with
the call's real start time and duration, so any OpenTelemetry backend (Jaeger, Tempo,
Honeycomb, Datadog...) shows OunceMCP calls next to the rest of a service. It uses the
OpenTelemetry API only: configure an SDK and exporter in the application as usual;
without one, spans are discarded at no cost.

Attributes follow the trace fields and are redacted the same way (SEC-11): a principal
digest, never the raw caller; no arguments unless the tracer records them, and then
redacted; no messages or results.
"""

from __future__ import annotations

import json
from typing import Any

from ouncemcp.trace import CallTrace

PREFIX = "ouncemcp."


def span_attributes(trace: CallTrace) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "mcp.method.name": "tools/call",
        "gen_ai.tool.name": trace.tool,
        f"{PREFIX}outcome": trace.outcome,
        f"{PREFIX}result_tokens": trace.result_tokens,
        f"{PREFIX}tokenizer": trace.tokenizer,
        f"{PREFIX}truncated": trace.truncated,
        f"{PREFIX}attempts": trace.attempts,
        f"{PREFIX}duration_ms": trace.duration_ms,
        f"{PREFIX}trace_id": trace.trace_id,
    }
    optional = {
        "budget": trace.budget,
        "effect": trace.effect,
        "error.category": trace.error_category,
        "error.code": trace.error_code,
        "principal": trace.principal,
        "parent": trace.parent,
        "resolved_by": trace.resolved_by,
    }
    for key, value in optional.items():
        if value is not None:
            attrs[f"{PREFIX}{key}"] = value
    if trace.replayed:
        attrs[f"{PREFIX}replayed"] = True
    if trace.truncation:
        attrs[f"{PREFIX}truncation"] = json.dumps(trace.truncation, sort_keys=True)
    if trace.arguments:
        attrs[f"{PREFIX}arguments"] = json.dumps(trace.arguments, sort_keys=True, default=str)
    return attrs


class OTelSink:
    """A trace sink that records each call as an OpenTelemetry span."""

    def __init__(self, tracer: Any = None, *, tracer_name: str = "ouncemcp") -> None:
        if tracer is None:
            from opentelemetry import trace as otel_trace

            tracer = otel_trace.get_tracer(tracer_name)
        self._tracer = tracer

    def emit(self, trace: CallTrace) -> None:
        from opentelemetry.trace import Status, StatusCode

        start_ns = int(trace.started_at * 1e9)
        end_ns = start_ns + int(trace.duration_ms * 1e6)
        span = self._tracer.start_span(
            f"tools/call {trace.tool}", start_time=start_ns, attributes=span_attributes(trace)
        )
        if trace.outcome == "error":
            span.set_status(Status(StatusCode.ERROR, trace.error_code or "error"))
        span.end(end_time=end_ns)
