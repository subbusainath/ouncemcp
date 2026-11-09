"""End-to-end over a real stdio transport: a subprocess server, as a host would run it."""

import sys

import pytest
from conftest import EXAMPLE
from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport

from ouncemcp import get_counter, measure_tools
from ouncemcp.core.costs import definition_payload
from ouncemcp.core.tokens import compact_json


@pytest.mark.anyio
async def test_stdio_definitions_match_cost_report_and_results_fit():
    from server import server

    transport = PythonStdioTransport(str(EXAMPLE / "server.py"), python_cmd=sys.executable)
    counter = get_counter()
    async with Client(transport) as client:
        wire = await client.list_tools()
        recount = sum(counter.count(compact_json(definition_payload(t))) for t in wire)
        report = await server.cost_report()
        assert recount == report.total == measure_tools(wire, server.budget).total

        r = await client.call_tool("inventory.search", {"region": "ap-south-1", "limit": 50})
        meta = r.meta["ouncemcp"]
        assert counter.count(r.content[0].text) == meta["result_tokens"] <= meta["budget"]

        page = await client.call_tool("inventory.search", {"region": "ap-south-1", "limit": 5})
        assert len(page.structured_content["items"]) == 5
        nxt = await client.call_tool(
            "inventory.search",
            {"region": "ap-south-1", "limit": 5, "cursor": page.structured_content["next_cursor"]},
        )
        assert nxt.structured_content["items"][0]["id"] != page.structured_content["items"][0]["id"]

        one = await client.call_tool("inventory.get", {"id": "da-00002"})
        assert "config" not in one.structured_content
        full = await client.call_tool(
            "inventory.get", {"id": "da-00002", "response_format": "detailed"}
        )
        assert "config" in full.structured_content
