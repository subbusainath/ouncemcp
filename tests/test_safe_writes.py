"""Safe writes: preview, approve, commit (proposal 0012, release gates 3 and 4)."""

from __future__ import annotations

from typing import Any

import mcp_types as mt
import pytest
from fastmcp import Client
from fastmcp.server.context import reset_transport, set_transport
from pydantic import BaseModel

from ouncemcp import Effect, OunceServer
from ouncemcp.execution.approval import APPROVAL_KEY, ApprovalRequest


class Refund(BaseModel):
    order: str
    amount: int


def build(effects: list[Refund], **kwargs: Any) -> OunceServer:
    server = OunceServer("shop", **kwargs.pop("server", {}))

    async def preview(order: str, amount: int) -> str:
        return f"Refund {amount} EUR on order {order}?"

    @server.tool(
        name="orders.refund",
        effect=Effect.WRITE,
        approval=kwargs.pop("approval", "required"),
        preview=preview,
        idempotency=True,
    )
    async def refund(order: str, amount: int) -> Refund:
        """Refund an order. Side effect: money is returned to the customer."""
        effects.append(Refund(order=order, amount=amount))
        return Refund(order=order, amount=amount)

    return server


def answering(approve: bool, seen: list[str]) -> Any:
    async def handler(message: str, response_type: Any, params: Any, context: Any) -> dict:
        seen.append(message)
        return {"approve": approve}

    return handler


@pytest.mark.anyio
async def test_approved_write_runs_once_after_the_person_saw_the_preview():
    effects: list[Refund] = []
    seen: list[str] = []
    server = build(effects)
    async with Client(server.fastmcp, elicitation_handler=answering(True, seen)) as client:
        result = await client.call_tool("orders.refund", {"order": "A-1", "amount": 40})
    assert result.structured_content == {"order": "A-1", "amount": 40}
    assert seen == ["Refund 40 EUR on order A-1?"]
    assert len(effects) == 1


@pytest.mark.anyio
async def test_declined_write_changes_nothing():
    effects: list[Refund] = []
    server = build(effects)
    async with Client(server.fastmcp, elicitation_handler=answering(False, [])) as client:
        result = await client.call_tool(
            "orders.refund", {"order": "A-1", "amount": 40}, raise_on_error=False
        )
    assert result.is_error
    assert result.meta["ouncemcp"]["error"] == "approval_declined"
    assert effects == []


@pytest.mark.anyio
async def test_conditional_approval_only_asks_for_large_amounts():
    effects: list[Refund] = []
    seen: list[str] = []

    def large(request: ApprovalRequest) -> bool:
        return request.arguments["amount"] > 1000

    server = build(effects, approval=large)
    async with Client(server.fastmcp, elicitation_handler=answering(True, seen)) as client:
        await client.call_tool("orders.refund", {"order": "A-1", "amount": 40})
        await client.call_tool("orders.refund", {"order": "A-2", "amount": 5000})
    assert seen == ["Refund 5000 EUR on order A-2?"]
    assert len(effects) == 2


@pytest.mark.anyio
async def test_a_receipt_cannot_be_replayed_or_moved_to_other_arguments():
    effects: list[Refund] = []
    server = build(effects)
    yes = {APPROVAL_KEY: {"action": "accept", "content": {"approve": True}}}
    async with Client(server.fastmcp, elicitation_handler=answering(True, [])) as client:
        session = client.session
        first = await session.call_tool(
            name="orders.refund",
            arguments={"order": "A-1", "amount": 40},
            allow_input_required=True,
        )
        assert isinstance(first, mt.InputRequiredResult)
        receipt = first.request_state
        done = await session.call_tool(
            name="orders.refund",
            arguments={"order": "A-1", "amount": 40},
            input_responses=yes,
            request_state=receipt,
            allow_input_required=True,
        )
        refused = [
            await refused_call(session, args, yes, receipt)
            for args in (
                {"order": "A-1", "amount": 40},  # the same request again
                {"order": "A-1", "amount": 9999},  # different amount
                {"order": "A-3", "amount": 40},  # different order
            )
        ]
    assert not done.is_error
    assert refused == [True, True, True]
    assert [e.order for e in effects] == ["A-1"]


async def refused_call(session: Any, args: dict, answers: dict, receipt: Any) -> bool:
    """True when the server refuses: an error result, or a protocol error from FastMCP's
    own request-state check. A second ``input_required`` is not a refusal."""
    from mcp.shared.exceptions import MCPError

    try:
        result = await session.call_tool(
            name="orders.refund",
            arguments=args,
            input_responses=answers,
            request_state=receipt,
            allow_input_required=True,
        )
    except MCPError:
        return True
    return bool(getattr(result, "is_error", False))


@pytest.mark.anyio
async def test_a_forged_answer_without_a_receipt_only_asks_again():
    effects: list[Refund] = []
    server = build(effects)
    yes = {APPROVAL_KEY: {"action": "accept", "content": {"approve": True}}}
    async with Client(server.fastmcp, elicitation_handler=answering(True, [])) as client:
        forged = await client.session.call_tool(
            name="orders.refund",
            arguments={"order": "A-1", "amount": 40},
            input_responses=yes,
            allow_input_required=True,
        )
    assert isinstance(forged, mt.InputRequiredResult)
    assert effects == []


@pytest.mark.anyio
async def test_without_a_way_to_ask_the_write_is_refused():
    effects: list[Refund] = []
    server = build(effects)
    result = await server.call_tool("orders.refund", {"order": "A-1", "amount": 40})
    assert result.is_error
    assert result.meta["ouncemcp"]["error"] == "approval_unavailable"
    assert effects == []


@pytest.mark.anyio
async def test_a_server_approver_decides_when_the_client_cannot_ask():
    effects: list[Refund] = []
    asked: list[str] = []

    async def approver(request: ApprovalRequest) -> bool:
        asked.append(request.summary)
        return request.arguments["amount"] < 100

    server = build(effects, server={"approver": approver})
    ok = await server.call_tool("orders.refund", {"order": "A-1", "amount": 40})
    no = await server.call_tool("orders.refund", {"order": "A-2", "amount": 400})
    assert not ok.is_error
    assert no.meta["ouncemcp"]["error"] == "approval_declined"
    assert asked == ["Refund 40 EUR on order A-1?", "Refund 400 EUR on order A-2?"]
    assert [e.order for e in effects] == ["A-1"]


@pytest.mark.anyio
async def test_approvals_are_enforced_on_stdio_even_when_stdio_is_trusted():
    effects: list[Refund] = []
    server = build(effects)
    token = set_transport("stdio")
    try:
        async with Client(server.fastmcp, elicitation_handler=answering(False, [])) as client:
            result = await client.call_tool(
                "orders.refund", {"order": "A-1", "amount": 40}, raise_on_error=False
            )
    finally:
        reset_transport(token)
    assert result.is_error and effects == []


def test_bad_approval_values_are_rejected():
    server = OunceServer("s")
    with pytest.raises(ValueError, match="approval"):

        @server.tool(name="t.w", effect=Effect.WRITE, approval="sometimes")  # type: ignore[arg-type]
        async def w() -> Refund:
            """Write."""
            return Refund(order="", amount=0)
