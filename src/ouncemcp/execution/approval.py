"""Approvals for safe writes: preview, approve, commit (proposal 0012, release gate 4).

``@server.tool(effect=Effect.WRITE, approval="required", preview=fn)`` asks a person
before the handler runs, so nothing can change before the answer (SEC-7 by
construction). ``approval`` can also be a function of the request, such as "only for
amounts over 1,000".

How the question reaches a person depends on the client:

- MCP 2026-07-28 clients get an ``input_required`` result with a confirmation and a
  signed receipt bound to the tool, the arguments and the caller. The retry must carry
  an accepted answer and the receipt, which is consumed in the store, so it cannot be
  replayed or moved to other arguments.
- Older clients that support elicitation are asked directly.
- Clients that can do neither are refused (``approval_unavailable``) unless the server
  has an ``approver`` hook (a ticket queue, a chat bot) that decides instead.

Approvals are enforced on every transport, stdio included. The model cannot approve a
write by itself: there is no argument for it (``approval`` and ``approved`` are reserved
parameter names), and only a client's answer to the server's own request counts.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ouncemcp.core.effects import Effect
from ouncemcp.core.errors import ErrorCategory, OunceError
from ouncemcp.security.identity import Identity

APPROVAL_KEY = "ouncemcp_approval"
STATE_KIND = "approval"
MAX_SUMMARY = 500


@dataclass(frozen=True)
class ApprovalRequest:
    """What an approval decision is about."""

    tool: str
    effect: Effect
    arguments: Mapping[str, Any]
    identity: Identity
    summary: str


ApprovalRule = Literal["never", "required"] | Callable[[ApprovalRequest], bool | Awaitable[bool]]
Approver = Callable[[ApprovalRequest], bool | Awaitable[bool]]
Preview = Callable[..., str | Awaitable[str]]


class ApprovalDeclined(OunceError):
    category: ErrorCategory = "permission"
    code = "approval_declined"

    def __init__(self) -> None:
        super().__init__(
            "The user did not approve this action, so nothing was changed.",
            hint="Ask the user what they want instead; do not retry the same action.",
        )


class ApprovalUnavailable(OunceError):
    category: ErrorCategory = "permission"
    code = "approval_unavailable"

    def __init__(self) -> None:
        super().__init__(
            "This action needs a person's approval, and this client cannot ask for it. "
            "Nothing was changed.",
            hint="Use a client that supports elicitation, or ask the server owner.",
        )


def check_rule(value: object, tool: str) -> ApprovalRule:
    if value in ("never", "required") or callable(value):
        return value  # type: ignore[return-value]
    raise ValueError(
        f"Tool '{tool}': approval must be 'never', 'required' or a function of ApprovalRequest"
    )


async def _call(fn: Callable[..., Any], *args: Any) -> Any:
    out = fn(*args)
    return await out if inspect.isawaitable(out) else out


async def needed(rule: ApprovalRule, request: ApprovalRequest) -> bool:
    """Whether this call needs approval. A rule that fails requires approval."""
    if rule == "never":
        return False
    if rule == "required":
        return True
    try:
        return (await _call(rule, request)) is not False
    except Exception:  # noqa: BLE001 - fail closed: ask
        return True


def default_summary(tool: str, effect: Effect, arguments: Mapping[str, Any]) -> str:
    shown = json.dumps(dict(arguments), default=str, separators=(", ", ": "))
    if len(shown) > 300:
        shown = shown[:299] + "…"
    return f"Allow {tool} ({effect.value}) with {shown}?"


async def summarize(
    preview: Callable[[], Any] | None, tool: str, effect: Effect, arguments: Mapping[str, Any]
) -> str:
    """The text the person approves: the tool's ``preview`` or a default summary."""
    if preview is None:
        return default_summary(tool, effect, arguments)
    text = str(await _call(preview))
    return text[:MAX_SUMMARY]


def confirmation(summary: str) -> dict[str, Any]:
    """The elicitation the client shows to the person."""
    return {
        "method": "elicitation/create",
        "params": {
            "message": summary,
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "approve": {
                        "type": "boolean",
                        "title": "Approve",
                        "description": "Allow this action",
                    }
                },
                "required": ["approve"],
            },
        },
    }


def accepted(response: Any) -> bool:
    """True only for an accepted elicitation whose ``approve`` is true."""
    if hasattr(response, "model_dump"):
        response = response.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(response, Mapping) or response.get("action") != "accept":
        return False
    content = response.get("content")
    return isinstance(content, Mapping) and content.get("approve") is True


def elicited_yes(result: Any) -> bool:
    """True for an accepted legacy ``ctx.elicit`` answer of yes."""
    if getattr(result, "action", None) != "accept":
        return False
    data = getattr(result, "data", None)
    if isinstance(data, Mapping):
        data = data.get("approve", data.get("value"))
    return data is True
