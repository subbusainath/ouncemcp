"""Server middleware: request limits, per-principal concurrency, and error mapping."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

import mcp_types as mt
from fastmcp.server.context import Context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from fastmcp.tools.base import InputRequiredToolResult

from ouncemcp.core.effects import Effect
from ouncemcp.core.errors import classify
from ouncemcp.execution.limits import ConcurrencyGate, Limits, check_request
from ouncemcp.trace import Tracer

# Facts the per-tool wrapper reports back for the trace (attempts, write resolution).
CALL_NOTES: ContextVar[dict[str, Any] | None] = ContextVar("ouncemcp_call_notes", default=None)
_CURRENT_TOOL: ContextVar[str | None] = ContextVar("ouncemcp_current_tool", default=None)


class OunceMiddleware(Middleware):
    """Wraps every tool call.

    It refuses oversized, over-nested or over-concurrent requests before they reach a
    handler (``principal_of`` names the caller that concurrency is counted against), and turns every exception that escapes the call path into a categorized,
    redacted error result. That covers failures FastMCP raises itself, such as argument
    validation and unknown tool names, which the per-tool wrapper never sees.
    """

    def __init__(
        self,
        *,
        limits: Limits,
        gate: ConcurrencyGate,
        effect_of: Callable[[str], Effect | None],
        principal_of: Callable[[str, Context | None], str],
        tracer: Tracer | None = None,
        count: Callable[[str], int] | None = None,
        tokenizer: str = "",
    ) -> None:
        self._tracer = tracer
        self._count = count
        self._tokenizer = tokenizer
        self._limits = limits
        self._gate = gate
        self._effect_of = effect_of
        self._principal_of = principal_of
        self._holding_slot: ContextVar[bool] = ContextVar("ouncemcp_holding_slot", default=False)

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        if self._tracer is None:
            return await self._call(context, call_next)
        name = context.message.name
        parent = _CURRENT_TOOL.get()
        notes: dict[str, Any] = {}
        notes_token = CALL_NOTES.set(notes)
        tool_token = _CURRENT_TOOL.set(name)
        started, clock = time.time(), time.perf_counter()
        try:
            result = await self._call(context, call_next)
        finally:
            CALL_NOTES.reset(notes_token)
            _CURRENT_TOOL.reset(tool_token)
        self._trace(context, result, notes, parent, started, time.perf_counter() - clock)
        return result

    def _trace(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        result: ToolResult,
        notes: dict[str, Any],
        parent: str | None,
        started: float,
        elapsed: float,
    ) -> None:
        assert self._tracer is not None
        name = context.message.name
        info: dict[str, Any] = dict((result.meta or {}).get("ouncemcp") or {})
        if isinstance(result, InputRequiredToolResult):
            outcome, tokens = "input_required", 0
        else:
            outcome = "error" if result.is_error else "ok"
            reported = info.get("result_tokens")
            if isinstance(reported, int):
                tokens = reported
            else:
                text = "".join(getattr(c, "text", "") for c in result.content or [])
                tokens = self._count(text) if self._count else 0
        effect = self._effect_of(name)
        try:
            principal: str | None = self._principal_of(name, context.fastmcp_context)
        except Exception:  # noqa: BLE001 - a resolver failure must not break tracing
            principal = None
        self._tracer.record(
            principal=principal,
            arguments=context.message.arguments,
            tool=name,
            outcome=outcome,
            started_at=started,
            duration_ms=round(elapsed * 1000, 3),
            result_tokens=tokens,
            tokenizer=str(info.get("tokenizer") or self._tokenizer),
            budget=info.get("budget"),
            truncated=bool(info.get("truncated")),
            attempts=int(notes.get("attempts", 1)),
            effect=effect.value if effect is not None else None,
            error_category=info.get("category") if result.is_error else None,
            error_code=info.get("error") if result.is_error else None,
            parent=parent,
            resolved_by=info.get("resolved_by"),
        )

    async def _call(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        name = context.message.name
        try:
            check_request(context.message.arguments, self._limits)
            if self._holding_slot.get():
                # A call made from inside a call, such as ``call_tool`` proxying a tool,
                # is part of that call and does not take a second slot.
                return await call_next(context)
            principal = self._principal_of(name, context.fastmcp_context)
            with self._gate.slot(principal):
                marker = self._holding_slot.set(True)
                try:
                    return await call_next(context)
                finally:
                    self._holding_slot.reset(marker)
        except Exception as exc:  # noqa: BLE001 - every failure becomes an error result
            return classify(
                exc,
                effect=self._effect_of(name),
                tool=name,
                max_chars=self._limits.max_error_chars,
            ).to_result()
