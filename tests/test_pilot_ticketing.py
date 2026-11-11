"""Pilot 2, protected ticketing workflow (spec 15.1): release gates 2 to 5 on a real
workflow, against the plain FastMCP version."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from ouncemcp.cli import main
from ouncemcp.cli.loader import load_server
from ouncemcp.eval import CrossTenant, GuessedName, run_security_suite
from ouncemcp.testing import as_caller

PILOT = Path(__file__).resolve().parents[1] / "examples" / "ticketing"
ACME = {"subject": "ana", "tenant": "acme"}
GLOBEX = {"subject": "gus", "tenant": "globex"}
ADMIN = {"subject": "root", "tenant": "acme", "scopes": ["tickets:admin"]}


@pytest.fixture
def pilot() -> Any:
    sys.path.insert(0, str(PILOT))
    load_server(str(PILOT / "server.py"))
    module = sys.modules["_ouncemcp_target_server"]
    upstream = sys.modules["upstream"]
    yield module, upstream
    sys.path.remove(str(PILOT))


def answer(approve: bool, seen: list[str]) -> Any:
    async def handler(message: str, response_type: Any, params: Any, context: Any) -> dict:
        seen.append(message)
        return {"approve": approve}

    return handler


@pytest.mark.anyio
async def test_timeout_after_the_upstream_write_produces_no_duplicate(pilot):
    module, upstream = pilot
    api = upstream.TicketAPI(latency_s=0.3).seed()
    server = module.build(api, write_deadline_ms=100)
    with as_caller(**ACME):
        first = await server.call_tool("tickets.create", {"title": "Monitor flickers"})
        assert first.meta["ouncemcp"]["error"] == "write_still_running"
        await server.idempotency.drain()
        retry = await server.call_tool("tickets.create", {"title": "Monitor flickers"})
    assert retry.structured_content["title"] == "Monitor flickers"
    assert retry.meta["ouncemcp"]["replayed"] is True
    assert api.writes == ["create T-5"]


@pytest.mark.anyio
async def test_closing_shows_the_exact_ticket_and_needs_a_yes(pilot):
    module, upstream = pilot
    api = upstream.TicketAPI().seed()
    server = module.build(api)
    seen: list[str] = []
    with as_caller(**ACME):
        async with Client(server.fastmcp, elicitation_handler=answer(False, seen)) as client:
            declined = await client.call_tool(
                "tickets.close", {"ticket_id": "T-1"}, raise_on_error=False
            )
        async with Client(server.fastmcp, elicitation_handler=answer(True, seen)) as client:
            closed = await client.call_tool("tickets.close", {"ticket_id": "T-1"})
    assert declined.meta["ouncemcp"]["error"] == "approval_declined"
    assert closed.structured_content["status"] == "closed"
    assert seen[0] == "Close ticket T-1: 'Printer on floor 3 is jammed'? The requester is notified."
    assert api.writes == ["close T-1"]


@pytest.mark.anyio
async def test_isolation_and_admin_only_delete(pilot):
    module, upstream = pilot
    api = upstream.TicketAPI().seed()
    server = module.build(api)
    report = await run_security_suite(
        server,
        [
            CrossTenant("tickets.search", {}, owner=ACME, intruder=GLOBEX),
            GuessedName("tickets.delete", intruder=ACME, arguments={"ticket_id": "T-1"}),
        ],
    )
    assert report.passed, report.render_text()
    with as_caller(**GLOBEX):
        async with Client(server.fastmcp, elicitation_handler=answer(True, [])) as client:
            other = await client.call_tool(
                "tickets.close", {"ticket_id": "T-1"}, raise_on_error=False
            )
    assert other.meta["ouncemcp"]["category"] == "not_found"
    with as_caller(**ADMIN):
        async with Client(server.fastmcp, elicitation_handler=answer(True, [])) as client:
            gone = await client.call_tool("tickets.delete", {"ticket_id": "T-2"})
    assert gone.structured_content == {"id": "T-2", "deleted": True}
    assert api.writes == ["delete T-2"]


@pytest.mark.anyio
async def test_a_token_without_a_tenant_is_refused(pilot):
    module, upstream = pilot
    server = module.build(upstream.TicketAPI().seed())
    with as_caller(subject="nobody"):
        result = await server.call_tool("tickets.search", {})
    assert result.meta["ouncemcp"]["category"] == "permission"


def test_pilot_passes_check(capsys):
    assert main(["check", str(PILOT / "server.py")]) == 0
    assert "tickets.close" in capsys.readouterr().out


@pytest.mark.anyio
async def test_the_plain_version_writes_twice_on_a_retry(pilot):
    """The baseline: a retried create runs again, and closing never asks anyone."""
    sys.modules.pop("_ouncemcp_target_plain_server", None)
    plain = load_server(f"{PILOT / 'plain_server.py'}:mcp")
    api = sys.modules["_ouncemcp_target_plain_server"].api
    async with Client(plain) as client:
        await client.call_tool("create_ticket", {"title": "Monitor flickers"})
        await client.call_tool("create_ticket", {"title": "Monitor flickers"})
        await client.call_tool("close_ticket", {"ticket_id": "T-1"})
    assert api.writes == ["create T-5", "create T-6", "close T-1"]
