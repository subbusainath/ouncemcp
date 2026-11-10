"""Server middleware: request limits, per-principal concurrency, and error mapping."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

import mcp_types as mt
from fastmcp.server.context import Context
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult

from ouncemcp.core.effects import Effect
from ouncemcp.core.errors import classify
from ouncemcp.execution.limits import ConcurrencyGate, Limits, check_request


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
    ) -> None:
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
