"""No irreversible effect before an input-required return (spec SEC-7).

In MCP 2026-07-28 a tool asks the client for input by returning ``input_required``.
The client answers by sending the whole ``tools/call`` again, and the handler runs from
the start. Anything the first run did before asking is therefore done twice unless the
handler is replay-safe. ``ToolContext.require_input`` refuses to ask after
``ToolContext.effect_applied()`` on a write or destructive tool that was not registered
with ``replay_safe=True``, and ``ouncemcp check`` flags handlers that ask after an effect.
"""

from __future__ import annotations

from typing import Any


class InputRequired(Exception):
    """Raised by ``ctx.require_input``; the server returns ``result`` to the client."""

    def __init__(self, result: Any) -> None:
        super().__init__("input required")
        self.result = result


class UnsafeInputRequest(RuntimeError):
    """A write tool asked for input after changing something (a server bug, not a
    caller mistake, so the caller sees an internal error with a reference id)."""

    def __init__(self, tool: str) -> None:
        super().__init__(
            f"Tool '{tool}' asked for input after ctx.effect_applied(). The client answers "
            "by repeating the call, so the effect would happen twice. Ask before the "
            "effect, or register the tool with replay_safe=True if repeating is harmless "
            "(spec SEC-7)."
        )
